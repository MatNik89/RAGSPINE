; ATLAS Windows installer — OPCIJA B (jedan installer, uloga na 1. ekranu).
; Kompajlira se na Windowsu (Inno Setup 6). Pi ne može ovo prevesti — zato je
; ovo skripta-deliverable, a ATLAS-strani health hook (/health) je testiran u CI.
;
; Uloge:
;   - Glavno računalo (server): instalira ATLAS + Windows servis + shortcut.
;   - Radna stanica (agent): veže se na server (SERVER PRVI — provjera /health),
;     upiše token (iz Postavke → Uređaji), registrira autostart agenta.
;
; Payload (build korak, prije Compile):
;   payload\python\        embedded Python + venv s instaliranim `atlas`
;   payload\atlas\         izvorni paket (ili wheel)
;   Assemble: uv/pip install . u payload\python, pa Compile ovu skriptu.

#define AppName "ATLAS"
#define AppVer "1.0.0"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\ATLAS
DefaultGroupName=ATLAS
DisableProgramGroupPage=yes
OutputBaseFilename=atlas-setup
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
Source: "payload\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs
Source: "payload\atlas\*";  DestDir: "{app}\atlas";  Flags: recursesubdirs createallsubdirs

[Code]
var
  RolePage: TInputOptionWizardPage;
  ServerPage: TInputQueryWizardPage;

const
  ROLE_SERVER = 0;
  ROLE_WORKSTATION = 1;

procedure InitializeWizard;
begin
  RolePage := CreateInputOptionPage(wpWelcome,
    'Uloga ovog računala', 'Što je ovo računalo?',
    'Server se instalira PRVI (glavno računalo). Radne stanice se vežu na njega.',
    True, False);
  RolePage.Add('Glavno računalo (ATLAS server)');
  RolePage.Add('Radna stanica (agent — veže se na server)');
  RolePage.SelectedValueIndex := ROLE_SERVER;

  ServerPage := CreateInputQueryPage(RolePage.ID,
    'Poveži na ATLAS server', 'Adresa glavnog računala + token uređaja',
    'Upiši adresu servera (npr. https://192.168.1.10:8443) i token izdan u ' +
    'ATLAS-u: Postavke → Uređaji.');
  ServerPage.Add('Adresa servera:', False);
  ServerPage.Add('Token uređaja:', False);
  ServerPage.Add('Ključ potpisa (sign key):', False);
  ServerPage.Values[0] := 'https://192.168.1.10:8443';
end;

{ Server PRVI: radna stanica ide dalje tek kad server odgovori atlas=true i
  setup_complete=true. Provjera preko /health (WinHTTP, ignorira self-signed
  jer cert još možda nije u trust storeu). }
function ServerHealthy(BaseUrl: string): Boolean;
var
  Http: Variant;
  Body: string;
begin
  Result := False;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    { 0x3300 = ignoriraj sve cert greške (LAN self-signed) }
    Http.Open('GET', BaseUrl + '/health', False);
    Http.SetTimeouts(3000, 3000, 3000, 4000);
    Http.Option(4) := 13056;
    Http.Send('');
    if Http.Status = 200 then begin
      Body := Http.ResponseText;
      Result := (Pos('"atlas":true', LowerCase(StringReplace(Body, ' ', '', [rfReplaceAll]))) > 0)
            and (Pos('"setup_complete":true', LowerCase(StringReplace(Body, ' ', '', [rfReplaceAll]))) > 0);
    end;
  except
    Result := False;
  end;
end;

{ Token = "<broj>.<url-safe>" — strogi format blokira argument-injection kroz
  [Run] command line (npr. token s navodnicima/razmakom/--config). }
function ValidToken(s: string): Boolean;
var i, dot: Integer; ch: Char;
begin
  Result := False; dot := 0;
  for i := 1 to Length(s) do begin
    ch := s[i];
    if ch = '.' then begin
      if (dot > 0) or (i = 1) then Exit;   { točno jedna točka, ne na početku }
      dot := i;
    end else if not ( ((ch >= '0') and (ch <= '9')) or ((ch >= 'A') and (ch <= 'Z'))
                   or ((ch >= 'a') and (ch <= 'z')) or (ch = '_') or (ch = '-') ) then
      Exit;   { samo znamenke/slova/_/- }
  end;
  Result := (dot > 0) and (Length(s) - dot >= 20);   { tajna barem 20 znakova }
end;

function ValidUrl(s: string): Boolean;
begin
  Result := (Pos('https://', LowerCase(s)) = 1) and (Pos(' ', s) = 0) and (Pos('"', s) = 0);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { preskoči server-stranicu ako je uloga = server }
  if (CurPageID = RolePage.ID) and (RolePage.SelectedValueIndex = ROLE_SERVER) then
    Exit;
  if CurPageID = ServerPage.ID then begin
    if not ValidUrl(Trim(ServerPage.Values[0])) then begin
      MsgBox('Adresa servera mora biti https:// bez razmaka.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if not ValidToken(Trim(ServerPage.Values[1])) then begin
      MsgBox('Neispravan token. Kopiraj ga točno iz Postavke → Uređaji.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if Trim(ServerPage.Values[2]) = '' then begin
      MsgBox('Upiši ključ potpisa (sign key) — bez njega agent ne provjerava potpise.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if not ServerHealthy(Trim(ServerPage.Values[0])) then begin
      MsgBox('Ne mogu potvrditi ATLAS server na toj adresi. Provjeri je li ' +
             'GLAVNO RAČUNALO već instalirano i postavljeno, pa pokušaj ponovno.',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { radna-stanica config-stranica se preskače na serveru }
  Result := (PageID = ServerPage.ID) and (RolePage.SelectedValueIndex = ROLE_SERVER);
end;

function IsServer: Boolean;
begin
  Result := RolePage.SelectedValueIndex = ROLE_SERVER;
end;

function ServerUrl(Param: string): string;   begin Result := Trim(ServerPage.Values[0]); end;
function DeviceToken(Param: string): string;  begin Result := Trim(ServerPage.Values[1]); end;
function SignKey(Param: string): string;      begin Result := Trim(ServerPage.Values[2]); end;

[Run]
; --- Glavno računalo: instaliraj ATLAS servis (koji vrti `atlas serve`) + cert ---
Filename: "{app}\python\python.exe"; Parameters: "-m atlas servis install"; \
  Check: IsServer; Flags: runhidden; StatusMsg: "Instaliram ATLAS servis..."
; otvori nadzornu ploču — `atlas open` sam izračuna ispravan URL (host/port + cert),
; NE `serve` (to bi pokrenulo drugi server pored servisa)
Filename: "{app}\python\python.exe"; Parameters: "-m atlas open"; \
  Check: IsServer; Description: "Otvori ATLAS nadzornu ploču"; \
  Flags: postinstall nowait skipifsilent

; --- Radna stanica: postavi agenta prema serveru (server-first već provjeren) ---
; dontlogparameters: token NE smije završiti u installer /LOG datoteci (Codex nalaz)
Filename: "{app}\python\python.exe"; \
  Parameters: "-m atlas.agent.install --server ""{code:ServerUrl}"" --token ""{code:DeviceToken}"" --sign-key ""{code:SignKey}"""; \
  Check: not IsServer; Flags: runhidden dontlogparameters; StatusMsg: "Postavljam ATLAS agenta..."
