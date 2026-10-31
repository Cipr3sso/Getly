# Regole di Rete e Resilienza HTTP per il Download Engine

## 1. Multi-Interface Socket Binding su Windows (Winsock IP_UNICASTIF)
- Quando si implementa l'aggregazione di banda o il routing multi-interfaccia su Windows, non affidarsi al semplice bind su IP sorgente (`local_addr`), poiché il *Weak Host Model* del kernel instraderebbe comunque i pacchetti sull'interfaccia con la metrica di routing più bassa.
- Configurare sempre il socket prima della connessione utilizzando l'opzione Winsock `IP_UNICASTIF` (opzione 31 su `IPPROTO_IP`):
  ```python
  import socket
  import struct
  # if_index deve essere codificato in network byte order (big-endian)
  sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", adapter.if_index))
  ```
- Ricavare l'`IfIndex` in modo affidabile tramite le API Win32 native (`iphlpapi.dll` -> `GetAdaptersAddresses`).

## 2. Forzatura Stack IPv4 in aiohttp (`family=socket.AF_INET`)
- Su connettori `aiohttp.TCPConnector` associati a specifici IP locali IPv4, forzare SEMPRE la famiglia di indirizzi a `family=socket.AF_INET`:
  ```python
  connector = aiohttp.TCPConnector(
      family=socket.AF_INET,
      local_addr=(adapter.ip, 0),
      socket_factory=socket_factory,
      force_close=False,
      keepalive_timeout=60.0,
  )
  ```
- Evita che il resolver o l'algoritmo aiohappyeyeballs eseguano query DNS per record IPv6 (AAAA), che fallirebbero sistematicamente su host dual-stack con `[Errno 11001] getaddrinfo failed`.

## 3. Gestione HTTP 429 (Too Many Requests) & Exponential Backoff
- Quando si riceve un codice HTTP 429:
  1. **Rispetto di `Retry-After`**: Ispezionare la risposta; se presente l'header `Retry-After`, parsare sia la durata numerica in secondi sia il formato `HTTP-date` (`email.utils.parsedate_to_datetime`) e attendere il tempo indicato prima di ritentare.
  2. **Backoff con Jitter**: In assenza dell'header, applicare un exponential backoff con jitter casuale:
     ```python
     delay = min(2 ** attempt + random.uniform(0.5, 1.5), 30.0)
     await asyncio.sleep(delay)
     ```
  3. **Retry Locale al Worker**: Il worker deve ritentare internamente fino a un massimo di tentativi (es. 3-5) senza rimettere immediatamente il chunk nella coda globale per evitare loop ravvicinati.
  4. **Ripresa Parziale**: In caso di disconnessione mid-stream o timeout, preservare `chunk.bytes_downloaded` e richiedere esclusivamente i byte rimanenti (`bytes={start + downloaded}-{end}`).
  5. **Keep-Alive e Riutilizzo Connessioni**: Non inviare `Connection: close`, mantenere `keepalive_timeout` configurato sul connector e abilitare il riutilizzo dei socket per ridurre gli handshake TLS/TCP consecutivi.
