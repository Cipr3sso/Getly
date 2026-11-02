# Dispatch Log

## 2026-11-02T11:00:28Z
Esegui una revisione completa e coordinata su tutti i livelli di Getly (Frontend WinUI 3, Backend Python e Browser Extension Manifest V3), suddividendo il lavoro nei 4 workstream definiti di seguito.

Working directory: c:\Users\carlo\Documents\antigravity\resilient-heisenberg
Integrity mode: development

## Contesto Ambiente e Repository
- Repository: c:\Users\carlo\Documents\antigravity\resilient-heisenberg
- Branch: main (remote: https://github.com/Cipr3sso/Getly.git)
- Python runtime: C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe
- .NET runtime: C:\Users\carlo\.dotnet\dotnet.exe (net8.0-windows10.0.19041.0)
- WinUI 3 test suite: dotnet test tests/Getly.UI.Tests -r win-x64
- Python test suite: pytest tests/

## Requirements

### R1. Frontend WinUI 3 — Temi Dialog, Floating Hub Multi-Task, Impostazioni & Batch UI (`src/Getly.UI`)
- **Allineamento Tema Chiaro/Scuro su tutti i `ContentDialog` (`MainWindow.xaml.cs`)**:
  - Creare un metodo helper centralizzato `private void PrepareDialog(ContentDialog dialog)` che imposta `dialog.XamlRoot = root.XamlRoot;` e `dialog.RequestedTheme = root.ActualTheme;` verificando `this.Content is FrameworkElement root`.
  - Invocare `PrepareDialog(dialog)` prima di ogni `await dialog.ShowAsync()` in `MainWindow.xaml.cs` (inclusi "Nuovo Download", "Eliminare il download...", "Dettagli del Download").
- **Fix Grafico e Multi-Tasking Unificato in `FloatingDownloadWindow` (`Getly Mini`)**:
  - Evitare l'icona duplicata nella barra delle applicazioni applicando `AppWindow.IsShownInSwitchers = false;` prima dell'attivazione della finestra.
  - Correggere la sovrapposizione tra testo di errore/velocità e stato nella riga superiore di `FloatingDownloadWindow.xaml` utilizzando una `Grid` a 2 colonne (`Column 0: Width="*"`, `Column 1: Width="Auto"`), applicando `TextTrimming="CharacterEllipsis"`, `MaxLines="1"` e `Margin="0,0,12,0"` sul `TextBlock` di sinistra per troncare con ellissi messaggi lunghi senza sovrapporsi alla dicitura di stato a destra.
  - Implementare navigazione multi-download e cassetto espandibile: gestire una collezione `ObservableCollection<TaskItemViewModel> ActiveTasks` nella singola istanza della finestra; aggiungere nell'header i controlli `◀` `1 / N` `▶` per scorrere i task e il pulsante `▼` per espandere/comprimere l'elenco (altezza da 175px a 350px con lista compatta dotata di `CheckBox`, nome file, peso in MB e barra inferiore con pulsanti per selezionare tutti, avviare o eliminare i selezionati).
  - Gestire lo stato di conferma pre-download (`WaitingConfirmation`): nel footer mostrare i pulsanti `Avvia Ora`, `In Coda` e `Annulla`, oltre al percorso di destinazione con pulsante `Modifica`.
- **Impostazioni Soglia Conferma e Auto-Smistamento (`MainWindow.xaml` & `MainViewModel.cs`)**:
  - Nella sezione "Download & Percorsi" di `SettingsView`, aggiungere `ToggleSwitch` `RequireConfirmation` (default: `false`), `NumberBox` `ConfirmationThresholdMB` (default: `100`, Min: `0`, Max: `100000`) e `ToggleSwitch` `AutoCategorize` (default: `true`).
  - Persistere tali impostazioni e sincronizzarle con il backend Python via IPC/configurazione.
- **Finestra Download Batch (`+ Download Batch`)**:
  - Nella `CommandBar` di `MainWindow.xaml`, accanto a `+ Nuovo Download`, aggiungere il pulsante `📦 Download Batch`.
  - Al click, aprire un `ContentDialog` tematizzato con `PrepareDialog` provvisto di: `TextBox` multilinea per incollare elenchi di URL o testo grezzo; pulsante "Analizza URL" per estrarre via RegExp link `http/https/magnet` popolando una lista con `CheckBox`, nome file dedotto e badge categoria; pulsanti di azione "Avvia Selezionati", "Aggiungi in Coda", "Annulla".

### R2. Core Python — Fix "Download ended incompletely", Cloudflare Streaming & Batch API (`core/`)
- **Risoluzione di `Download ended incompletely` (0 B di 15 MB) e coerenza header anti-bot (`probe.py`, `downloader.py`, `worker.py`, `session_pool.py`)**:
  - Non sovrascrivere `User-Agent` o inserire header `Sec-Ch-Ua` hardcoded (es. `"124"`) se il client/estensione ha fornito gli header autentici del browser; nel fallback con `curl_cffi.requests.AsyncSession(impersonate="chrome")`, eliminare dagli header custom eventuali `User-Agent` o `Sec-Ch-Ua` discordanti con il profilo TLS impersonato, preservando `Cookie` (incluso `cf_clearance`) e `Referer`.
  - In `worker.py`, con `curl_cffi`, usare `resp = await session.get(url, headers=req_headers, stream=True, allow_redirects=True)`. Validare `if resp.status_code >= 400: raise DownloadError(...)` prima di iterare su `resp.aiter_content()`.
  - In `downloader.py`, qualora richieste `Range` multi-connessione ricevano rifiuti HTTP (403/429/503), eseguire il downgrade automatico a 1 singola connessione sequenziale (senza header `Range`) completando il download anziché interrompere il task.
  - Se il download fallisce registrando 0 byte scaricati, memorizzare in `task.error_message` l'eccezione effettiva sollevata dal worker (es. `"HTTP 403 Forbidden (Cloudflare WAF)"`) e non l'errore generico `"Download ended incompletely"`.
- **Nuovo Endpoint `POST /add_batch` (`core/api/local_server.py`)**:
  - Esporre l'endpoint `POST /add_batch` che accetta `{ "items": [ { "url": "...", "filename": "...", "file_size": ..., "headers": {...} } ], "auto_start": true }`.
  - Inoltrare ogni elemento a `QueueManager` applicando lo smistamento automatico in sottocartelle per categoria e la soglia di conferma impostata.

### R3. Estensione Browser — Page Link Grabber & Allineamento Header (`extensions/chrome/`)
- **Allineamento Header e Client Hints (`background.js`)**:
  - Rimuovere l'invio di header `Sec-Ch-Ua` fittizi (`v="124"`) congiuntamente al `navigator.userAgent` reale; trasmettere `User-Agent` effettivo, `Referer` reale (della scheda attiva o del download) e header `Cookie` completo ottenuto tramite `extractFullCookieHeader`.
- **Funzionalità "Analizza tutti i link della pagina" (`manifest.json`, `popup.html`, `popup.js`, `background.js`)**:
  - Aggiungere il permesso `"scripting"` in `extensions/chrome/manifest.json`.
  - Nel popup dell'estensione integrare il pulsante `"🔍 Analizza tutti i link della pagina"`: eseguire `chrome.scripting.executeScript` sulla scheda attiva estraendo tutti i link da `<a href>`, `<video src>`, `<audio src>`, `<source src>`.
  - Filtrare i file scaricabili in base alle estensioni (.zip, .rar, .7z, .pdf, .mp4, .mkv, .iso, .exe, .msi, .apk, .tar.gz, ecc.).
  - Mostrare nel popup una barra di filtraggio rapido per categoria (Tutti, Archivi, Documenti, Video, Programmi), checkbox per ciascun elemento e pulsante `"⬇️ Scarica selezionati con Getly"` collegato all'invio verso `POST http://127.0.0.1:9666/add_batch`.

### R4. Verifica, Test e Validazione
- Eseguire la suite di test Python con `pytest tests/ -v`, comprensiva di nuovi test dedicati a `/add_batch` e al fallback automatico a connessione singola sequenziale su 403.
- Compilare la soluzione WinUI 3 tramite `dotnet build src/Getly.UI -c Debug -r win-x64` assicurando 0 errori e 0 warning.
- Eseguire i test WinUI 3 tramite `dotnet test tests/Getly.UI.Tests -r win-x64`.
- Validare l'adeguamento visivo del tema Dark su tutti i dialoghi e l'assenza di sovrapposizioni grafiche in `FloatingDownloadWindow`.
- Eseguire commit e push dei cambiamenti direttamente su branch `main` (`origin/main`).
