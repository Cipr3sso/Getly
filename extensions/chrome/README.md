# Getly WebExtension (Manifest V3)

Estensione browser ufficiale per **Getly Download Manager**:
- **Intercettazione automatica dei download**: Cattura i download avviati nel browser e li instrada direttamente su Getly per la suddivisione dinamica a segmenti paralleli e ripresa automatica.
- **Video & Media Sniffer**: Rileva flussi multimediali, video MP4, WebM e stream HLS (`.m3u8`) / DASH (`.mpd`) durante la riproduzione nelle pagine web.
- **Menu contestuale**: Tasto destro del mouse per scaricare singoli link, immagini, video o tutti i media presenti nella pagina.
- **Integrazione locale zero-config**: Comunica direttamente con il demone Getly in background tramite `http://127.0.0.1:9666/add`.

## Installazione in Google Chrome / Brave / Microsoft Edge
1. Apri `chrome://extensions/` (o `edge://extensions/`).
2. Abilita la **Modalità sviluppatore** (Developer mode) in alto a destra.
3. Clicca su **Carica estensione non pacchettizzata** (Load unpacked).
4. Seleziona questa cartella (`extensions/chrome`).
5. Assicurati che Getly sia avviato: l'icona dell'estensione mostrerà il badge verde *Getly v1.0*.
