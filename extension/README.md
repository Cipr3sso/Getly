# Guida Installazione Estensione Browser per Resilient Heisenberg

L'estensione WebExtension Manifest V3 consente l'intercettazione automatica dei download da qualsiasi browser basato su Chromium (Google Chrome, Microsoft Edge, Brave, Opera, Vivaldi, ecc.) e l'inoltro a **Resilient Heisenberg Download Manager**.

---

## 1. Registrazione del Native Messaging Host su Windows

Prima di caricare l'estensione nel browser, registra l'host nativo eseguendo da terminale nella root del progetto:

```powershell
python main.py --register-browser
```

Questo comando:
1. Genera lo script batch wrapper `native_messaging/resilient_downloader.bat`.
2. Genera il manifest del Native Messaging Host `native_messaging/resilient_downloader.json`.
3. Registra automaticamente le chiavi di registro di Windows sotto `HKEY_CURRENT_USER`:
   - `HKCU\Software\Google\Chrome\NativeMessagingHosts\resilient_downloader`
   - `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\resilient_downloader`
   - `HKCU\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\resilient_downloader`

---

## 2. Caricamento dell'Estensione nel Browser

### Google Chrome & Brave
1. Apri la barra degli indirizzi e vai su `chrome://extensions` (o `brave://extensions`).
2. Abilita la levetta **"Modalità sviluppatore"** (in alto a destra).
3. Clicca sul pulsante **"Carica estensione non compressa"** (Load unpacked).
4. Seleziona la cartella `extension/` presente all'interno del progetto.

### Microsoft Edge
1. Vai su `edge://extensions`.
2. Abilita la levetta **"Modalità sviluppatore"** (nella barra laterale sinistra o in alto a destra).
3. Clicca su **"Carica estensione non compressa"**.
4. Seleziona la cartella `extension/`.

---

## 3. Verifica Funzionamento

1. Clicca sull'icona dell'estensione nella barra degli strumenti del browser:
   - **Native Messaging**: deve mostrare il badge verde `Connesso`.
   - **Daemon Locale (9666)**: mostra `In Ascolto (9666)` se l'applicazione (`main.py`) è aperta, oppure `Offline (Fallback DB)` se l'applicazione è chiusa.
2. Avvia un qualsiasi download da una pagina web:
   - L'estensione cancella istantaneamente il download nativo del browser per evitare traffico duplicato.
   - Cattura URL, nome file, Referer, User-Agent e cookie di autenticazione di sessione.
   - Se l'applicazione è in esecuzione, il download appare subito nella GUI/Dashboard.
   - Se l'applicazione è chiusa, il file viene salvato direttamente nel database SQLite di stato (`.state.db`) e verrà scaricato appena apri il manager!
   - Se il Native Messaging Host è disabilitato o non risponde, l'estensione riavvia automaticamente il download normale del browser come fallback per non bloccare mai l'utente.
