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


def run_agent(spine, cfg, query: str, actor, llm, max_steps: int = 4) -> dict:
    tools = [{"name": t.name, "description": t.description, "schema": t.schema}
              for t in agent_tools.TOOLS.values()]
    messages = [{"role": "user", "content": query}]
    sources: list = []
    last_text = ""

    for _ in range(max_steps):
        result = llm.complete(messages, system=SYSTEM_PROMPT, tools=tools)
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
