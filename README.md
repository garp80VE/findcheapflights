# FindCheapFlights

Buscador de vuelos baratos que combina lo que Google Flights / Kayak / Skyscanner no te muestran juntos:

1. **Precio base real** vía Google Flights (sin API key).
2. **Costos ocultos estimados** — maletas, asientos, carry-on por aerolínea.
3. **Arbitraje por país (POS)** — busca el mismo vuelo desde 12 mercados (US, UK, ES, DE, MX, BR, IN, JP, TR, AR, CA, AU) por si el precio es más bajo en otra divisa.
4. **Fechas flexibles** — barrido de ±0-30 días alrededor de la fecha pedida.
5. **Aeropuertos alternativos cercanos** — si la ruta pedida no tiene vuelos, sugiere los 2 hubs cercanos (radio 1500 km) que sí tienen, con su precio.
6. **Recomendaciones de timing** — analiza si es buen momento para reservar (sweet spot por tipo de ruta, temporada alta, duración óptima).
7. **Tracker de precios + alerta por email** — guarda búsquedas, las revisa cada 6h en background y te avisa cuando bajan a tu precio objetivo o tocan mínimo histórico.
8. **🎯 Modo cazador** — tres técnicas que aggregator clásico no surface:
   - **Aeropuertos compuestos**: prueba en paralelo combinaciones (origen-cercano × destino-cercano) y muestra solo las más baratas que el primary.
   - **Hidden-city (skiplagging)**: busca rutas A→C que escalan en B (tu destino real) y son más baratas que A→B directo. Solo válido one-way.
   - **Amadeus API** (segunda fuente, opcional con API key).
9. **🎁 Feed de mistake fares / ofertas** — pull en background de Secret Flying y The Flight Deal cada 30 min con parser de origen/destino/precio.

Cada resultado trae un link directo al sitio de la aerolínea (Iberia, Ryanair, easyJet, Vueling, Wizz, AA, Delta, United, JetBlue, Southwest, Spirit, Frontier, LATAM, Avianca, Aeromexico, Volaris, Emirates, Qatar, Turkish, TAP, KLM, AF, BA, LH, Air Canada…) más fallbacks a Google Flights, Kayak y Skyscanner.

## Cómo arrancar (Windows / PowerShell)

```powershell
# Una sola vez:
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install fast-flights fastapi "uvicorn[standard]" httpx python-multipart

# Cada vez:
.\run.ps1
```

Abre <http://127.0.0.1:8765/>.

## Inputs aceptados

- **Origen / Destino**: códigos IATA (3 letras) — MAD, BCN, JFK, GIG, NRT…
- **Fechas**: ida obligatoria, vuelta opcional (si la dejas vacía busca solo-ida).
- **Pax**: adultos, niños (2-11), bebés (<2), adultos mayores (se cuentan como adultos).
- **Extras**: maletas facturadas por pax, asiento elegido sí/no.
- **Máx. escalas**: directo / 1 / 2 / cualquiera.
- **Flex ± días**: 0-7 (cada día son N peticiones extra → tarda).
- **Arbitraje por país**: 12 peticiones extra en paralelo → ~5-8s.

## Endpoints

```
GET    /                   # UI
GET    /healthz            # liveness + smtp_configured + amadeus_configured
GET    /api/airports       # lista de 6032 aeropuertos (cached 1 día)
POST   /api/search         # búsqueda principal — campos extra del cazador:
                           #   composite, hidden_city, use_amadeus (booleans)
GET    /api/deals          # mistake fares activos (?origin=XXX&destination=XXX)
POST   /api/tracks         # crear alerta de precio (ver app/main.py:TrackPayload)
GET    /api/tracks         # listar alertas con histórico de precios
DELETE /api/tracks/{id}    # eliminar alerta
```

## Configuración de email (opcional, para alertas)

El tracker corre sin email — las alertas se guardan en `data/tracks.db` y se ven en la UI. Para recibir email cuando un precio baja, configura SMTP antes de arrancar el servidor:

```powershell
# PowerShell — Gmail con App Password (Recommended setting)
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "tu.cuenta@gmail.com"
$env:SMTP_PASS = "abcd efgh ijkl mnop"   # App Password de 16 dígitos
$env:SMTP_FROM = "tu.cuenta@gmail.com"
$env:FCF_POLL_INTERVAL_S = "21600"        # opcional: cada cuánto chequear (def: 6h)
.\run.ps1
```

Gmail requiere [App Password](https://myaccount.google.com/apppasswords) — tu contraseña normal no funciona si tienes 2FA activado (lo cual deberías). Para otros proveedores (Outlook, Mailgun, SendGrid…) usa los mismos vars con los valores de su SMTP.

**Importante**: el server debe quedar corriendo para que las alertas se disparen. Si lo apagas, no se chequean precios — al volver a arrancar, retoma desde donde estaba (estado en SQLite).

## Modo cazador 🎯

Tres opciones que activas con checkboxes en el formulario:

### Aeropuertos compuestos
Prueba en paralelo combinaciones (3 aeropuertos cercanos a origen × 3 a destino, radio 400 km) y muestra sólo las que salen **más baratas que tu primary**. Ejemplo: pides MAD→BCN, te puede ofrecer ALC→GRO o VLC→BCN si Vueling/Ryanair lo tienen más barato. Costo: ~10s extra de búsquedas.

### Hidden-city (skiplagging)
Busca rutas A→C que **escalan** en tu destino real B y son más baratas que A→B directo. La idea: compras el ticket más largo y te bajas en la escala (B). **Riesgos**:
- Algunas aerolíneas anulan tu vuelta o banean la cuenta si lo detectan.
- Solo válido para ida — si lo haces de ida, la vuelta queda invalidada automáticamente.
- **Sin maletas facturadas** — irían al destino final, no a B.
- Verifica en la página de la aerolínea que el itinerario realmente escale en B antes de comprar.

Mapeo hub→destinos-onward en [app/hunter.py:_ONWARD_HUBS](app/hunter.py).

### Amadeus API (segunda fuente, opcional)
Cruza precios con [Amadeus Self-Service](https://developers.amadeus.com/) (free tier: 2000 calls/mes). A veces tiene inventario B2B que GF no agrega.

```powershell
$env:AMADEUS_CLIENT_ID = "..."
$env:AMADEUS_CLIENT_SECRET = "..."
# Para producción (paid):
$env:AMADEUS_BASE = "https://api.amadeus.com"
.\run.ps1
```

## Feed de mistake fares 🎁

En la parte inferior de la página verás "Ofertas activas" — pull en background cada 30 min de [Secret Flying](https://www.secretflying.com/) y [The Flight Deal](https://www.theflightdeal.com/). Cada deal lo parseo con regex para sacar origen/destino/precio y mapearlo a IATA con la base de aeropuertos.

Filtros via endpoint: `/api/deals?origin=MAD`, `/api/deals?destination=NYC`, `/api/deals?origin_country=Spain`.

Estos son **tarifas-error humanas** (ej. Cathay Pacific publicó SFO→Haikou a $853 en vez de los $2500 normales). No las verás en GF porque típicamente duran horas antes de que las corrijan.

Ejemplo:

```powershell
$body = @{
  origin = "MAD"; destination = "BCN"
  depart_date = "2026-06-10"; return_date = "2026-06-17"
  adults = 2; children = 1; infants = 0; seniors = 1
  checked_bags = 1; pick_seat = $true
  flex_days = 3; probe_pos = $true
  max_stops = 1
} | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8765/api/search -Method POST -ContentType application/json -Body $body
```

## Arquitectura

```
app/
├── main.py              # FastAPI + endpoints
├── search.py            # primary search + flex + POS + quick-probe
├── parser.py            # HTML -> ParsedFlight basado en aria-label
├── airports.py          # haversine + nearest()
├── hunter.py            # composite + hidden-city candidates
├── deals.py             # RSS scraper de mistake fares
├── amadeus.py           # cliente Amadeus (OAuth + flight-offers)
├── recommendations.py   # reglas de timing/temporada
├── tracker.py           # SQLite + scheduler en thread background
├── notifier.py          # SMTP (opcional)
├── fees.py              # estima carry-on / checked / seat
├── airlines.py          # deep-link builders a 25+ aerolíneas
├── currency.py          # conversión a USD vía frankfurter.app (ECB)
├── static/              # app.js, style.css
└── templates/           # index.html

data/
├── airports.json        # 6032 aeropuertos (IATA, city, country, name, lat, lon)
├── fees.json            # tabla editable con fees por aerolínea (USD)
└── tracks.db            # SQLite del tracker
```

### Por qué el parser usa `aria-label`

Google rota clases CSS cada pocas semanas. Cada `<li>` de un vuelo tiene un `aria-label` con el texto completo del itinerario en lenguaje natural:

> "From 78 US dollars. Nonstop flight with Iberia. Leaves Adolfo Suárez Madrid-Barajas Airport at 9:35 PM on Wednesday, June 10 and arrives at Josep Tarradellas Barcelona-El Prat Airport at 10:50 PM on Wednesday, June 10. Total duration 1 hr 15 min. Select flight"

El parser extrae con regex precio, divisa, aerolínea, escalas, horarios, aeropuertos y duración. Mucho más resistente a cambios de markup que perseguir clases ofuscadas.

### Por qué `hl=en` en todas las probes

Las queries de arbitraje varían `gl` (país) y `curr` (divisa) pero **fuerzan `hl=en`** para que el `aria-label` siga estando en inglés y el parser siga funcionando. Si quisieras precios localizados *y* parseo nativo, habría que añadir regexes por idioma.

## Limitaciones honestas

- **Google Flights no expone fees de equipaje** → la columna "extras" usa una tabla estática (`data/fees.json`) con valores best-effort. Edítala si conoces fees más precisas para una aerolínea/ruta. El "real" depende del tarifario (Basic vs Standard vs Plus…).
- **Arbitraje POS es informativo**: el precio existe, pero algunas aerolíneas anulan tickets comprados desde un país distinto al de origen del pasajero. Verifica T&Cs antes de comprar.
- **No es estable a largo plazo**: scrapea Google Flights con un cliente camuflado (primp + chrome impersonation). Si Google cambia el HTML, hay que actualizar los selectores/regex en `app/parser.py`.
- **Rate limits**: una búsqueda con flex_days=7 + probe_pos=true son ~25 peticiones a Google. Si lo usas mucho desde la misma IP, te puede empezar a rechazar.
- **Deep-links de aerolínea**: son URLs de búsqueda construidas con los parámetros del usuario. Algunas aerolíneas (las que mejor las soportan) caen directo en la página de resultados; otras te llevan al home pre-rellenado, y unas pocas ignoran los parámetros. Las que no están en `_AIRLINE_BUILDERS` caen al fallback de Google.

## Cómo extender

- **Añadir aerolínea low-cost con fees particulares** → `data/fees.json`.
- **Añadir deep-link a una aerolínea** → función nueva en `app/airlines.py:_AIRLINE_BUILDERS`.
- **Añadir mercado de arbitraje** → tupla en `app/search.py:POS_PROBES`.
- **Cambiar a una API de pago (Amadeus, Duffel)** → reemplazar `app/search.py:_fetch_one`. El resto (UI, fees, arbitraje, flex) sigue igual.
