"""Map airline display names to direct booking URLs."""
from __future__ import annotations
from urllib.parse import quote

# Each entry: builder(orig, dest, depart_iso, return_iso, adults, children, infants) -> url
# Where possible we deep-link straight into the airline's search results.


def _build_tfs(orig: str, dest: str, depart: str, ret: str | None,
               adults: int, children: int, infants: int) -> str:
    """Build the Google Flights tfs base64 token — the same protobuf-encoded
    payload that Google's own URLs use. With this, the link opens Google
    Flights with the search already executed, instead of a free-text query."""
    from fast_flights import FlightData, Passengers
    from fast_flights.filter import TFSData
    fd = [FlightData(date=depart, from_airport=orig, to_airport=dest)]
    if ret:
        fd.append(FlightData(date=ret, from_airport=dest, to_airport=orig))
    trip = "round-trip" if ret else "one-way"
    tfs = TFSData.from_interface(
        flight_data=fd, trip=trip, seat="economy",
        passengers=Passengers(adults=adults, children=children,
                              infants_in_seat=0, infants_on_lap=infants),
        max_stops=None,
    )
    return tfs.as_b64().decode("utf-8")


def google_flights_market_url(orig: str, dest: str, depart: str,
                              ret: str | None, adults: int, children: int,
                              infants: int, gl: str = "us", curr: str = "USD",
                              hl: str = "en") -> str:
    """Google Flights URL with the search pre-executed in a specific market.

    gl/hl/curr control which Google "point of sale" loads — so clicking a
    DE/EUR arbitrage card opens Google Flights with the same tfs token but in
    the German market and currency, surfacing the cheaper local-currency price.
    """
    try:
        tfs = _build_tfs(orig, dest, depart, ret, adults, children, infants)
        return (f"https://www.google.com/travel/flights?tfs={quote(tfs, safe='')}"
                f"&hl={hl}&gl={gl}&curr={curr}")
    except Exception:
        ret_q = f"%20returning%20{ret}" if ret else ""
        return (f"https://www.google.com/travel/flights?q=Flights%20from%20"
                f"{orig}%20to%20{dest}%20on%20{depart}{ret_q}&gl={gl}")


def _google_flights_link(orig: str, dest: str, depart: str, ret: str | None,
                         adults: int, children: int, infants: int) -> str:
    return google_flights_market_url(orig, dest, depart, ret,
                                     adults, children, infants)


def _kayak_link(orig, dest, depart, ret, adults, children, infants):
    base = f"https://www.kayak.com/flights/{orig}-{dest}/{depart}"
    if ret:
        base += f"/{ret}"
    qs = []
    if adults != 1: qs.append(f"adults={adults}")
    if children: qs.append(f"children={children}")
    if qs: base += "?" + "&".join(qs)
    return base


def _skyscanner_link(orig, dest, depart, ret, adults, children, infants):
    d = depart.replace("-", "")[2:]  # YYYY-MM-DD -> YYMMDD
    if ret:
        r = ret.replace("-", "")[2:]
        return f"https://www.skyscanner.net/transport/flights/{orig.lower()}/{dest.lower()}/{d}/{r}/?adults={adults}&children={children}&infants={infants}"
    return f"https://www.skyscanner.net/transport/flights/{orig.lower()}/{dest.lower()}/{d}/?adults={adults}&children={children}&infants={infants}"


# Airline-specific deep links (best-effort search-page URLs)
_AIRLINE_BUILDERS = {
    "ryanair":  lambda o,d,dep,ret,a,c,i: f"https://www.ryanair.com/es/es/trip/flights/select?adults={a}&teens=0&children={c}&infants={i}&dateOut={dep}&dateIn={ret or ''}&isConnectedFlight=false&isReturn={'true' if ret else 'false'}&discount=0&promoCode=&originIata={o}&destinationIata={d}&tpAdults={a}&tpTeens=0&tpChildren={c}&tpInfants={i}&tpStartDate={dep}&tpEndDate={ret or ''}&tpOriginIata={o}&tpDestinationIata={d}",
    "wizz air": lambda o,d,dep,ret,a,c,i: f"https://wizzair.com/en-gb/booking/select-flight/{o}/{d}/{dep}/{ret or ''}/{a}/{c}/{i}/null",
    "easyjet":  lambda o,d,dep,ret,a,c,i: f"https://www.easyjet.com/en/buyonline.mvc?dx={o}&ax={d}&dd={dep}&rd={ret or ''}&ADT={a}&CHD={c}&INF={i}&rt={'on' if ret else 'off'}",
    "vueling":  lambda o,d,dep,ret,a,c,i: f"https://tickets.vueling.com/SearchFlights.aspx?culture=en-GB&Trip={'RT' if ret else 'OW'}&O1={o}&D1={d}&DD1={dep}" + (f"&O2={d}&D2={o}&DD2={ret}" if ret else "") + f"&ADT={a}&CHD={c}&INF={i}",
    "iberia":   lambda o,d,dep,ret,a,c,i: f"https://www.iberia.com/es/?market=es&language=es#/booking/flights?trip={'rt' if ret else 'ow'}&from={o}&to={d}&out={dep}" + (f"&in={ret}" if ret else "") + f"&adults={a}&children={c}&infants={i}",
    "lufthansa": lambda o,d,dep,ret,a,c,i: f"https://www.lufthansa.com/us/en/flights-from-{o.lower()}-to-{d.lower()}?travelers={a}-{c}-{i}&travelDates={dep}_{ret or ''}",
    "klm":      lambda o,d,dep,ret,a,c,i: f"https://www.klm.com/search/offers?cabinClass=ECONOMY&adults={a}&children={c}&infants={i}&origin={o}&destination={d}&outboundDate={dep}" + (f"&inboundDate={ret}&tripType=ROUNDTRIP" if ret else "&tripType=ONEWAY"),
    "air france": lambda o,d,dep,ret,a,c,i: f"https://wwws.airfrance.us/search/advanced?adt={a}&chd={c}&inf={i}&from={o}&to={d}&depart={dep}" + (f"&return={ret}" if ret else ""),
    "british airways": lambda o,d,dep,ret,a,c,i: f"https://www.britishairways.com/travel/redeem/public/en_us?eId=106003#/booking/availability?ad={a}&yad=0&ch={c}&inf={i}&from1={o}&to1={d}&depart1={dep}" + (f"&return1={ret}" if ret else ""),
    "american":  lambda o,d,dep,ret,a,c,i: f"https://www.aa.com/booking/find-flights?type={'roundTrip' if ret else 'oneWay'}&adult={a}&child={c}&infantLap={i}&from={o}&to={d}&depart={dep}" + (f"&return={ret}" if ret else ""),
    "delta":     lambda o,d,dep,ret,a,c,i: f"https://www.delta.com/flight-search/book-a-flight?tripType={'ROUND_TRIP' if ret else 'ONE_WAY'}&priceSchedule=PRICE&originCity={o}&destinationCity={d}&departureDate={dep}" + (f"&returnDate={ret}" if ret else "") + f"&paxCount={a+c+i}",
    "united":    lambda o,d,dep,ret,a,c,i: f"https://www.united.com/en/us/fsr/choose-flights?f={o}&t={d}&d={dep}" + (f"&r={ret}" if ret else "") + f"&tt={'1' if ret else '2'}&px={a+c+i}",
    "jetblue":   lambda o,d,dep,ret,a,c,i: f"https://www.jetblue.com/booking/flights?from={o}&to={d}&depart={dep}" + (f"&return={ret}" if ret else "") + f"&isMultiCity=false&noOfRoute=1&adults={a}&children={c}&infants={i}",
    "southwest": lambda o,d,dep,ret,a,c,i: f"https://www.southwest.com/air/booking/select.html?adultPassengersCount={a}&departureDate={dep}" + (f"&returnDate={ret}" if ret else "") + f"&destinationAirportCode={d}&originationAirportCode={o}&tripType={'roundtrip' if ret else 'oneway'}",
    "spirit":    lambda o,d,dep,ret,a,c,i: f"https://www.spirit.com/book/flights?o1={o}&d1={d}&dd1={dep}" + (f"&dd2={ret}" if ret else "") + f"&ADT={a}&CHD={c}&INF={i}&tripType={'RT' if ret else 'OW'}",
    "frontier":  lambda o,d,dep,ret,a,c,i: f"https://booking.flyfrontier.com/Flight/InternetBooking?ARRIVAL1={d}&DEPARTURE1={o}&ADT={a}&CHD={c}&INF={i}&DDATE1={dep}" + (f"&DDATE2={ret}" if ret else ""),
    "latam":     lambda o,d,dep,ret,a,c,i: f"https://www.latamairlines.com/us/en/offers/flights?origin={o}&outbound={dep}&destination={d}" + (f"&inbound={ret}" if ret else "") + f"&adt={a}&chd={c}&inf={i}&trip={'RT' if ret else 'OW'}",
    "avianca":   lambda o,d,dep,ret,a,c,i: f"https://www.avianca.com/es/booking/select/?from={o}&to={d}&departure={dep}" + (f"&return={ret}" if ret else "") + f"&adults={a}&children={c}&infants={i}",
    "copa":      lambda o,d,dep,ret,a,c,i: f"https://shop.copaair.com/?roundTrip={'true' if ret else 'false'}&adults={a}&children={c}&infants={i}&from0={o}&to0={d}&departure0={dep}" + (f"&return0={ret}" if ret else ""),
    "aeromexico": lambda o,d,dep,ret,a,c,i: f"https://aeromexico.com/en-us/flights?origin={o}&destination={d}&departureDate={dep}" + (f"&returnDate={ret}" if ret else "") + f"&adt={a}&chd={c}&inf={i}",
    "volaris":   lambda o,d,dep,ret,a,c,i: f"https://www.volaris.com/en/book/flight/select?o1={o}&d1={d}&dd1={dep}" + (f"&dd2={ret}" if ret else "") + f"&ADT={a}&CHD={c}&INF={i}",
    "tap air portugal": lambda o,d,dep,ret,a,c,i: f"https://book.flytap.com/booking/flights/search?adults={a}&children={c}&infants={i}&from={o}&to={d}&depart={dep}" + (f"&return={ret}" if ret else ""),
    "ita airways": lambda o,d,dep,ret,a,c,i: f"https://www.ita-airways.com/en_en/fly-ita/flight-search?from={o}&to={d}&depart={dep}" + (f"&return={ret}" if ret else "") + f"&adults={a}&children={c}&infants={i}",
    "turkish airlines": lambda o,d,dep,ret,a,c,i: f"https://www.turkishairlines.com/en-us/flights/booking?adultCount={a}&childCount={c}&infantCount={i}&from={o}&to={d}&depart={dep}" + (f"&return={ret}" if ret else ""),
    "emirates":  lambda o,d,dep,ret,a,c,i: f"https://www.emirates.com/us/english/book/flight-search?origin={o}&destination={d}&date={dep}" + (f"&returnDate={ret}" if ret else "") + f"&adults={a}&children={c}&infants={i}",
    "qatar airways": lambda o,d,dep,ret,a,c,i: f"https://www.qatarairways.com/en/homepage.html?dispatch=showSearchResults&fromStation={o}&toStation={d}&departureDate={dep}" + (f"&returnDate={ret}" if ret else "") + f"&adults={a}&children={c}&infants={i}",
    "air canada":lambda o,d,dep,ret,a,c,i: f"https://www.aircanada.com/aem/en/aco/home/book/search-results?org0={o}&dest0={d}&departureDate0={dep}" + (f"&org1={d}&dest1={o}&departureDate1={ret}" if ret else "") + f"&numAdults={a}&numTotalPassengers={a+c+i}",
}


def build_booking_links(airline_name: str, orig: str, dest: str,
                        depart: str, ret: str | None,
                        adults: int, children: int, infants: int) -> dict:
    """Return {airline_direct, google, kayak, skyscanner} URLs.

    airline_direct may be None when we don't have a known builder.
    """
    name = (airline_name or "").lower()
    direct = None
    for key, builder in _AIRLINE_BUILDERS.items():
        if key in name:
            try:
                direct = builder(orig, dest, depart, ret, adults, children, infants)
            except Exception:
                direct = None
            break
    return {
        "airline_direct": direct,
        "google": _google_flights_link(orig, dest, depart, ret, adults, children, infants),
        "kayak": _kayak_link(orig, dest, depart, ret, adults, children, infants),
        "skyscanner": _skyscanner_link(orig, dest, depart, ret, adults, children, infants),
    }
