# Agentska petlja (Faza 3, T3): LLM bira alat iz agent_tools.TOOLS, read-only
# alati se izvrše odmah i rezultat vrati LLM-u; write alat se SAMO predloži
# (pending) — izvršenje čeka eksplicitnu potvrdu korisnika (T4: /chat/potvrdi).
#
# ponytail: format poruka natrag LLM-u je obična {"role","content": str}
# parica (ne puni Anthropic tool_result/tool_use blok) — LLMClient.complete
# poruke samo proslijedi provideru, a ovaj oblik je dovoljan da model vidi
# što se dogodilo i nastavi. Upgrade path: pravi tool_use/tool_result blokovi
# ako se pokaže da neki provider inzistira na strogom formatu.
import json

from atlas.rag import agent_tools

SYSTEM_PROMPT = """Ti si ATLAS, asistent u računovodstvenom uredu.

Pomažeš radnicima: pretražuješ interno znanje i po potrebi internet, daješ
popis mjesečnih obveza (PDV, JOPPD...) i cjeloviti dosje klijenta. Kad je
potrebna promjena podataka, možeš predložiti: dodavanje klijenta, uređivanje
podataka klijenta, označavanje obveze kao poslane, zakazivanje roka isteka
ili bilješku uz klijenta.

VAŽNO PRAVILO: za svaku promjenu podataka SAMO predloži akciju i pričekaj
izričitu potvrdu korisnika — nikad ne tvrdi da je promjena već napravljena
dok korisnik ne potvrdi. Poštuj ovlasti radnika: ako alat javi da radnja nije
dopuštena ili klijent ne postoji, prenesi to korisniku umjesto nagađanja."""


PENDING_TTL_MIN = 10


def stash_pending(spine, actor, pending: dict) -> str:
    """Spremi predloženi WRITE kao jednokratni vlasnički token (LLM izlaz je
    nepovjerljiv: ništa se ne izvrši dok korisnik ne potvrdi). Vrati token."""
    import secrets
    token = secrets.token_urlsafe(24)
    with spine.write() as c:
        c.execute("INSERT INTO agent_pending(token,user_id,org_id,tool,args_json) "
                  "VALUES(?,?,?,?,?)",
                  (token, actor.user_id, actor.org_id, pending["tool"],
                   json.dumps(pending["args"], ensure_ascii=False)))
    return token


def confirm_pending(spine, cfg, token: str, actor, ttl_min: int = PENDING_TTL_MIN) -> dict:
    """Atomično potroši vlasnički, ne-istekli pending token i IZVRŠI alat. Ovlasti
    se PONOVO provjere u run_tool sad (ne na vrijeme prijedloga). Dijeljeno između
    web /chat/potvrdi i Telegram inline potvrde. Vrati {tool, result}. Diže
    ValueError ('nepostoji'/domenska greška iz run_tool)."""
    with spine.write() as c:
        row = c.execute(
            "SELECT tool, args_json FROM agent_pending "
            "WHERE token=? AND user_id=? AND org_id=? AND created_at > datetime('now', ?)",
            (token, actor.user_id, actor.org_id, f"-{ttl_min} minutes")).fetchone()
        if row is None:
            raise ValueError("prijedlog ne postoji ili je istekao")
        c.execute("DELETE FROM agent_pending WHERE token=?", (token,))
        tool, args_json = row["tool"], row["args_json"]
    result = agent_tools.run_tool(spine, cfg, actor, tool, json.loads(args_json))
    spine.audit(actor.username, "agent_execute", tool, args_json)
    return {"tool": tool, "result": result}


def cancel_pending(spine, token: str, actor) -> None:
    with spine.write() as c:
        c.execute("DELETE FROM agent_pending WHERE token=? AND user_id=? AND org_id=?",
                  (token, actor.user_id, actor.org_id))


def summarize_action(name: str, args: dict) -> str:
    """Ljudski hrvatski sažetak predložene write akcije, za potvrdu."""
    if name == "dodaj_klijenta":
        oib = f" (OIB {args['oib']})" if args.get("oib") else ""
        return f"Dodat ću klijenta {args.get('naziv', '')}{oib}."
    if name == "uredi_klijenta":
        polja = ", ".join(f"{k}={v}" for k, v in (args.get("polja") or {}).items())
        return f"Uredit ću klijenta {args.get('kljuc', '')}: {polja}."
    if name == "oznaci_obvezu":
        stanje = "poslano" if args.get("stanje", True) else "nije poslano"
        period = args.get("period") or "tekući period"
        return (f"Označit ću obvezu {args.get('vrsta', '')} za "
                f"{args.get('klijent', '')} ({period}) kao {stanje}.")
    if name == "zakazi_rok":
        return (f"Zakazat ću rok {args.get('vrsta', '')} za "
                f"{args.get('klijent', '')} na {args.get('datum', '')}.")
    if name == "zapisi_belesku":
        tekst = args.get("tekst", "")
        kratko = tekst if len(tekst) <= 60 else tekst[:57] + "..."
        return f"Zapisat ću bilješku uz klijenta {args.get('klijent', '')}: \"{kratko}\"."
    if name == "dodaj_vrstu_obveze":
        freq = args.get("frequency", "monthly")
        return (f"Dodat ću/urediti vrstu obveze {args.get('label') or args.get('kind', '')} "
                f"({freq}, rok {args.get('rule', '—')}, za {args.get('applies_to', 'sve aktivne')}).")
    if name == "nauci_izvor":
        return f"Naučit ću s web-stranice: {args.get('url', '')}."
    if name == "pokreni_program":
        return f"Pokrenut ću {args.get('program', '')} na stanici radnika {args.get('radnik', '')}."
    if name == "posalji_poruku_klijentu":
        return f"Poslat ću poruku klijentu {args.get('klijent', '')}: \"{args.get('naslov', '')}\"."
    return f"Izvršit ću akciju {name} s argumentima {args}."


def _echo(result_text: str, name: str) -> str:
    """Asistentova poruka koju vraćamo u kontekst — nikad prazna (neki
    provideri odbijaju prazan tekstni blok)."""
    return result_text or f"(poziv alata {name})"


def _accumulate_sources(sources: list, name: str, tool_result: dict) -> None:
    if name != "pretrazi" or not isinstance(tool_result, dict):
        return
    for hit in tool_result.get("lokalno") or []:
        sources.append({"n": len(sources) + 1, "title": hit.get("naslov"), "doc_id": hit.get("doc_id")})


def _skills_catalog_text(spine, actor) -> str:
    """Katalog aktivnih vještina (samo ime+opis) za system-prompt — progresivno
    otkrivanje: pune korake agent povlači alatom ucitaj_vjestinu kad zatrebaju."""
    from atlas.knowledge import skills as skills_mod
    rows = skills_mod.list_skills(spine, actor.org_id, status="active")
    if not rows:
        return ""
    lines = "\n".join(f"- {s['name']}: {s['description']}" for s in rows if s.get("name"))
    if not lines:
        return ""
    return ("\n\nDostupne vještine (procedure ureda) — pozovi alat "
            "ucitaj_vjestinu(ime) da učitaš pune korake kad su relevantne:\n" + lines)


def run_agent(spine, cfg, query: str, actor, llm, max_steps: int = 4) -> dict:
    # pokaži SAMO alate koje uloga smije — model tako ne predloži zabranjeni alat
    # pa lažno ne tvrdi da ga je izvršio (iskrenost umj. tihog pada; OpenWorker obrazac)
    tools = [{"name": t.name, "description": t.description, "schema": t.schema}
              for t in agent_tools.TOOLS.values() if agent_tools.allowed(actor, t)]
    system = SYSTEM_PROMPT + _skills_catalog_text(spine, actor)  # katalog vještina (progresivno)
    messages = [{"role": "user", "content": query}]
    sources: list = []
    last_text = ""

    for _ in range(max_steps):
        result = llm.complete(messages, system=system, tools=tools)
        last_text = result.text

        if not result.tool_calls:
            return {"text": result.text, "sources": sources, "pending": None}

        call = result.tool_calls[0]
        name, args = call.get("name"), call.get("args") or {}
        tool = agent_tools.TOOLS.get(name)

        if tool is None:
            messages.append({"role": "assistant", "content": _echo(result.text, name)})
            messages.append({"role": "user", "content":
                              f"Nepoznat alat: {name!r}. Dostupni alati: "
                              f"{', '.join(sorted(agent_tools.TOOLS))}."})
            continue

        if tool.readonly:
            try:
                tool_result = agent_tools.run_tool(spine, cfg, actor, name, args)
            except ValueError as e:
                messages.append({"role": "assistant", "content": _echo(result.text, name)})
                messages.append({"role": "user", "content": f"Greška pri pozivu alata {name}: {e}"})
                continue
            _accumulate_sources(sources, name, tool_result)
            messages.append({"role": "assistant", "content": _echo(result.text, name)})
            messages.append({"role": "user", "content":
                              f"Rezultat alata {name}: "
                              f"{json.dumps(tool_result, ensure_ascii=False, default=str)}"})
            continue

        # write alat: NE izvršavaj — samo validiraj i predloži (čeka potvrdu)
        if not agent_tools.allowed(actor, name):
            messages.append({"role": "assistant", "content": _echo(result.text, name)})
            messages.append({"role": "user", "content": f"Nemate ovlasti za alat {name}."})
            continue
        ok, err = agent_tools.validate(name, args)
        if not ok:
            messages.append({"role": "assistant", "content": _echo(result.text, name)})
            messages.append({"role": "user", "content":
                              f"Nevažeći argumenti za {name}: {err}. Ispravi i pokušaj ponovno."})
            continue

        summary = summarize_action(name, args)
        return {"text": summary, "sources": sources,
                "pending": {"tool": name, "args": args, "summary": summary}}

    return {"text": last_text or "Nisam uspio dovršiti zahtjev unutar dopuštenog broja koraka.",
            "sources": sources, "pending": None}
