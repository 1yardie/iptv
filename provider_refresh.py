"""Resolve anonymous LocalNow playback with public channel IDs.

The caller injects fetch_text(url, headers=None) and controls timeouts/retries.
Public web bootstrap token is read lazily at runtime; never commit or log it.
No login credentials or location override is supplied. Refresh each sync because
provider URLs/configuration can change; no fixed URL lifetime is advertised.
"""
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

PUBLIC_PAGE = 'https://localnow.com/channels'
XUMO_ENDPOINT = 'https://ottcms.weathergroup.com/api/v1/scte35stream/XumoV3'
PRIVATE_FIELDS = {'ip', 'ip_addr', 'ip_address', 'client_ip', 'device_ip', 'did', 'device_id', 'deviceid', 'ifa', 'idfa', 'adid', 'advertisingid', 'advertising_id', 'ssid', 'session', 'sessionid', 'session_id', 'playbackid', 'user_id', 'userid'}
AUTH_FIELDS = {'jwt', 'token', 'access_token', 'auth', 'authorization', 'signature', 'sig', 'expires', 'exp'}


def sanitize_url(url, channel_id=None):
    """Remove caller identifiers, retain exact public channel routing UUID.

    ads.nN/ads.vN encode named ad parameters; inspect their logical names too.
    ads.playbackid looks session-like but equals the public channel UUID in
    LocalNow responses and is required for routing. Accept only that exact ID.
    Unexpected auth/session playback data fails rather than being published.
    """
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Unexpected provider URL scheme or credentials')
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    by_key = {key.lower():value for key,value in pairs}
    if len(by_key) != len(pairs):
        raise ValueError('Duplicate provider query key')
    keep, removed = [], []
    for key, value in pairs:
        suffix = key.lower().removeprefix('ads.')
        numbered = re.fullmatch(r'ads\.([nv])(\d+)', key, flags=re.I)
        logical = (by_key.get('ads.n' + numbered.group(2), '').lower() if numbered else suffix)
        if suffix == 'playbackid' or logical == 'playbackid':
            routing_value = by_key.get('ads.v' + numbered.group(2), '') if numbered else value
            if channel_id and routing_value.upper() == channel_id.upper():
                keep.append((key,value))
                continue
            raise ValueError('Unexpected playback identifier')
        if (suffix in AUTH_FIELDS or logical in AUTH_FIELDS) and value:
            raise ValueError('Unexpected auth or expiry parameter in provider URL')
        if suffix in PRIVATE_FIELDS or logical in PRIVATE_FIELDS:
            removed.append(key)
        else:
            keep.append((key, value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(keep), '')), removed


def name_key(value):
    return re.sub(r'[^a-z0-9]', '', re.sub(r'^EPG\s*[|:]?\s*', '', value, flags=re.I).casefold())


def title_matches(expected, actual):
    if name_key(expected) == name_key(actual):
        return True
    words = expected.split()
    if not words:
        return False
    call = words[0]
    return bool(re.fullmatch(r'[WK][A-Z]{2,3}', call) and actual.split() and actual.split()[0] == call)


class LocalNowResolver:
    def __init__(self, fetch_text):
        self.fetch_text = fetch_text
        self.config = None
        self.channels = {}
        self._load_error = None

    def _load(self):
        if self._load_error is not None:
            raise ValueError(self._load_error)
        try:
            self._read_bootstrap()
        except Exception as exc:
            self._load_error = "LocalNow public bootstrap unavailable: " + type(exc).__name__
            raise ValueError(self._load_error) from exc

    def _read_bootstrap(self):
        if self.config is not None:
            return
        html = self.fetch_text(PUBLIC_PAGE, headers={'User-Agent':'Mozilla/5.0'})
        match = re.search(r'<script\b[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S)
        if not match:
            raise ValueError('LocalNow bootstrap data missing')
        page = json.loads(match.group(1))
        config = page['runtimeConfig']
        host = config['DSP_API_URL']
        if not re.fullmatch(r'[a-z0-9.-]+\.api\.cms\.amdvids\.com', host):
            raise ValueError('Unexpected LocalNow playback API host')
        token_data = config['DSP_TOKEN']
        token = (json.loads(token_data) if isinstance(token_data,str) else token_data)['token']
        if not isinstance(token,str) or not token:
            raise ValueError('LocalNow public bootstrap token missing')
        channels = {c['_id']:c for c in page['props']['pageProps']['page']['channels']}
        self.api_base = 'https://' + host
        self.token = token
        self.channels = channels
        self.config = config

    def resolve(self, channel_id, expected_title):
        if not re.fullmatch(r'[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}', channel_id):
            raise ValueError('Unexpected channel ID')
        self._load()
        channel = self.channels.get(channel_id)
        page_url = PUBLIC_PAGE + ('/' + channel['slug'] if channel else '')
        endpoint = self.api_base + '/video/play/' + channel_id + '/1920/1080'
        # The website passes page_url already percent-escaped as a query value.
        params = {'page_url':quote(page_url,safe=''), 'device_devicetype':'desktop_web',
                  'app_version':'0.0.0','app_bundle':'web.localnow','ssid':''}
        body = self.fetch_text(endpoint + '?' + urlencode(params), headers={
            'x-access-token':self.token, 'User-Agent':'Mozilla/5.0',
            'Origin':'https://localnow.com','Referer':'https://localnow.com/'})
        data = json.loads(body)
        if not isinstance(data,dict):
            raise ValueError('Unexpected provider response')
        if data.get('_id','').upper()!=channel_id.upper() or not title_matches(expected_title,str(data.get('title',''))):
            raise ValueError('Provider channel identity mismatch')
        if data.get('locked') or data.get('is_teaser') or data.get('drm_config') or data.get('geocheck') is False:
            raise ValueError('Provider returned a restricted/protected response')
        if not isinstance(data.get('video_m3u8'),str):
            raise ValueError('Provider returned no HLS URL')
        clean, removed = sanitize_url(data['video_m3u8'], channel_id)
        return {'url':clean, 'provider_channel_id':channel_id, 'provider_title':data['title'],
                'provenance':endpoint, 'page':page_url, 'removed_query_keys':removed,
                'catalog_present':channel is not None}


def resolve_xumo_localnow(fetch_text, source_url):
    # The upstream public client key may change. Read its current resolver URL,
    # never store the key here. Keep the public upstream local-programming ZIP
    # selection; omit caller-IP override because it is private client data.
    source = urlsplit(source_url)
    if (source.scheme != 'https' or source.hostname != 'ottcms.weathergroup.com'
            or source.path != '/api/v1/scte35stream/XumoV3'
            or source.username or source.password or source.port is not None):
        raise ValueError('Unexpected Xumo Local Now resolver endpoint')
    params = [(key,value) for key,value in parse_qsl(source.query,keep_blank_values=True)
              if key.lower() != 'reqargs.clientip']
    if not any(key.lower() == 'reqargs.apikey' and value for key,value in params):
        raise ValueError('Xumo Local Now public client key missing')
    resolver_url = urlunsplit((source.scheme,source.netloc,source.path,urlencode(params),''))
    url = json.loads(fetch_text(resolver_url, headers={'User-Agent':'Mozilla/5.0'}))
    if not isinstance(url,str):
        raise ValueError('Local Now endpoint returned no URL string')
    query = dict(parse_qsl(urlsplit(url).query,keep_blank_values=True))
    channel_name = query.get('ads.channel_name','')
    if not channel_name.startswith('DIST-Local_Now_'):
        raise ValueError('Local Now identity parameters missing')
    clean, removed = sanitize_url(url)
    return {'url':clean, 'provider_channel_id':query.get('ads.channel_id',''),
            'provider_title':'Local Now', 'provenance':XUMO_ENDPOINT,
            'removed_query_keys':removed, 'regional_channel_name':channel_name}
