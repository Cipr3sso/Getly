# Resilient Downloader (Python asyncio/aiohttp + Flet GUI)

Un Download Manager asincrono e modulare avanzato con supporto per:
1. **Desktop GUI Reattiva in Flet (Fluent Dark Theme)**: Dashboard moderna, schede con barre di avanzamento fluida, velocità (MB/s), ETA stimato e azioni rapide (Pausa, Ripresa, Annulla, Riprova, "Apri cartella").
2. **Multi-Interface Socket Binding ("Net-Merge")**: Aggregazione di banda contemporanea su schede di rete multiple (Wi-Fi, Ethernet, tethering cellulare) tramite Winsock `IP_UNICASTIF`.
3. **Net-Merge Live Monitor**: Schermata con throughput in tempo reale per singola scheda, percentuale di contribuzione al totale e switch per escludere o includere al volo le interfacce dal pool.
4. **System Tray & Window Lifecycle su Windows**: Minimizzazione alla chiusura ('X') nella traybar con `pystray`, menu contestuale rapido e notifiche Toast al completamento dei download.
5. **Clipboard Link Grabber**: Monitor asincrono in background degli appunti con rilevamento automatico di link di download e dialog di conferma non-intrusivo.
6. **Filecrypt & Click'n'Load Engine**: Server HTTP locale su `127.0.0.1:9666` conforme a CNL2, con sandbox JavaScript isolata (QuickJS/Dukpy) e routine crittografica AES-128-CBC.
7. **Download Engine & Positional Storage**: Slicing del file tramite HTTP `Range`, coda I/O dedicata (`Dedicated I/O Queue`) per scritture sequenziali senza race condition su Windows/Linux, allocazione preliminare sparse e ripristino crash-resilient (SQLite WAL).

---

## Architettura del Progetto

```text
resilient-heisenberg/
├── config/
│   └── settings.py               # Pydantic Settings
├── core/
│   ├── network/
│   │   ├── models.py             # Modello NetworkAdapter
│   │   ├── interface_detector.py # Rilevamento avanzato schede (Windows GetAdaptersAddresses / Linux)
│   │   ├── socket_binder.py      # Gestione Winsock IP_UNICASTIF (opt 31) e Linux SO_BINDTODEVICE
│   │   └── session_pool.py       # Pool ClientSession con toggle dinamico abilitazione schede
│   ├── cnl/
│   │   ├── models.py             # Schemi Pydantic per CNL2Payload e CNLDecryptedPackage
│   │   ├── decryptor.py          # Sandbox QuickJS/Dukpy + Decifratore AES-128-CBC
│   │   └── server.py             # Server aiohttp su :9666 con CORS preflight (OPTIONS)
│   ├── engine/
│   │   ├── probe.py              # Ispezione risorsa (HEAD / Range 0-0, Browser Headers, Dynamic Referer)
│   │   ├── chunk_planner.py      # Slicing adattivo e coda Dynamic Work-Stealing
│   │   ├── storage.py            # Dedicated I/O Queue, allocazione preliminare sparse Win32
│   │   ├── worker.py             # Worker con exponential backoff + jitter e rispetto Retry-After su 429
│   │   └── downloader.py         # Coordinatore del download e metriche per scheda
│   └── scheduler/
│       ├── task.py               # Modello di stato DownloadTask e DownloadStatus
│       ├── state_store.py        # Database SQLite WAL per il resume crash-resilient
│       └── queue_manager.py      # Concorrenza batch, gestione pause/resume/cancel e dispatcher CNL
├── ui/
│   ├── app.py                    # Setup applicazione Flet, routing e header di navigazione
│   ├── coordinator.py            # Reactive State & Metrics Coordinator con ticker asincrono (4-10 FPS)
│   ├── tray.py                   # Pystray Windows System Tray & notifiche Toast
│   ├── clipboard_watcher.py      # Monitor appunti per link grabber automatico
│   ├── components/
│   │   ├── download_card.py      # Card reattiva del task con progress, ETA e bottoni azione
│   │   ├── interface_card.py     # Card Net-Merge con throughput per scheda e switch on/off
│   │   ├── add_download_dialog.py# Dialog rapido per inserimento URL
│   │   └── clipboard_dialog.py   # Dialog di conferma per link intercettati
│   └── views/
│       ├── dashboard_view.py     # Schermata principale (lista task con filtri Tutti/Attivi/Completati)
│       └── monitor_view.py       # Vista approfondita Net-Merge throughput live
├── tests/
│   ├── test_network.py           # Test di rilevamento schede e socket binding
│   ├── test_cnl.py               # Test decifratura CNL2 e server HTTP
│   ├── test_engine.py            # Test storage posizionale, 429 backoff e mock download
│   ├── test_scheduler.py         # Test persistenza SQLite e dispatcher coda
│   └── test_ui_coordinator.py    # Test coordinatore UI, toggle schede e clipboard filtering
├── main.py                       # Entrypoint con supporto GUI (default) e CLI headless
└── pyproject.toml
```

---

## Installazione delle Dipendenze

```bash
pip install -r pyproject.toml
# oppure
pip install aiohttp cryptography pydantic psutil quickjs dukpy flet flet-desktop pystray pyperclip Pillow pytest pytest-asyncio
```

---

## Guida all'Utilizzo

### 1. Avvio GUI Desktop (Default)
Avvia la moderna applicazione desktop in dark-mode con server CNL2 attivo su `:9666`, link grabber e system tray:
```bash
python main.py
```

### 2. Elenco Schede di Rete Rilevate per il Net-Merge
```bash
python main.py --interfaces
```

### 3. Avvio Demone Headless CLI (Senza Interfaccia Grafica)
```bash
python main.py --cli
```

### 4. Download Diretto da URL da Terminale
```bash
python main.py --url "https://speed.hetzner.de/100MB.bin" --dir "C:\Users\carlo\Downloads"
```

---

## Esecuzione dei Test di Verifica

Tutti i 26 test unitari e di integrazione possono essere eseguiti con:
```bash
python -m pytest tests/ -v
```
