from flask import Flask, Response, request, redirect
import re
import requests
import os
import time
import threading
from bs4 import BeautifulSoup
from urllib.parse import urlparse

app = Flask(__name__)

ALLOWED_DOMAINS = [
    "player.dzsecurity.net",
    "hls-distrib-eu0.dzsecurity.net"
]

CHANNELS_DATA = {
    "echorouktv": {
        "embed_url": "https://player.dzsecurity.net/embed/echorouktv",
        "referer": "https://www.echoroukonline.com/",
        "cached_chunks_url": None,
    },
    "echorouknews": {
        "embed_url": "https://player.dzsecurity.net/embed/echorouknews",
        "referer": "https://www.echoroukonline.com/",
        "cached_chunks_url": None,
    },
    "ennahartv": {
        "embed_url": "https://player.dzsecurity.net/embed/ennahartv",
        "referer": "https://www.ennaharonline.com/",
        "cached_chunks_url": None,
    },
    "elhayattv": {
        "embed_url": "https://player.dzsecurity.net/embed/elhayattv",
        "referer": "https://elhayat.dz/",
        "cached_chunks_url": None,
    },
}

# CDN bağlantılarını açık tutmak (Keep-Alive) için global session
global_session = requests.Session()

def fetch_token_for_channel(channel_key):
    channel_info = CHANNELS_DATA[channel_key]
    embed_url = channel_info["embed_url"]
    original_referer = channel_info["referer"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": original_referer,
        "Origin": original_referer.rstrip("/"),
    }

    try:
        resp = global_session.get(embed_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        main_tag = soup.find("main", {"data-player": True})
        if not main_tag:
            return None

        channel = main_tag.get("data-channel", channel_key)
        embed_grant = main_tag.get("data-embed-grant")

        challenge_url = f"https://player.dzsecurity.net/api/playback/challenge/{channel}"
        api_headers = {
            "Content-Type": "application/json",
            "X-Embed-Grant": embed_grant,
            "Accept": "application/json",
            "Referer": embed_url,
            "Origin": "https://player.dzsecurity.net",
        }

        challenge_resp = global_session.post(challenge_url, headers=api_headers, json={}, timeout=10)
        if challenge_resp.status_code != 200:
            return None

        payload = challenge_resp.json()
        if not payload.get("ok"):
            return None

        data = payload.get("data", {})
        nonce = data.get("nonce")
        image_path = data.get("image_url")

        if image_path:
            img_url = f"https://player.dzsecurity.net{image_path}"
            global_session.get(img_url, headers={"Referer": embed_url}, timeout=10)

        sign_url = f"https://player.dzsecurity.net/api/playback/sign/{channel}"
        sign_resp = global_session.post(sign_url, headers=api_headers, json={"nonce": nonce}, timeout=10)
        
        if sign_resp.status_code != 200:
            return None

        sign_payload = sign_resp.json()
        if not sign_payload.get("ok"):
            return None

        m3u8_url = sign_payload.get("data", {}).get("url")
        m3u8_response = global_session.get(m3u8_url, headers=headers, timeout=15, allow_redirects=False)
        stream_url = m3u8_response.headers.get("Location")
        
        if not stream_url:
            stream_url = m3u8_url

        chunks_url = re.sub(r'playlist\.m3u8', 'chunks.m3u8', stream_url)
        return chunks_url

    except Exception as e:
        print(f"[{channel_key}] Token güncellenirken hata: {e}")
        return None

def update_all_tokens():
    print("[*] Kanalların linkleri yenileniyor...")
    for key in CHANNELS_DATA:
        new_url = fetch_token_for_channel(key)
        if new_url:
            CHANNELS_DATA[key]["cached_chunks_url"] = new_url
            print(f"[✓] Güncellendi: {key}")
        else:
            print(f"[-] Güncellenemedi (eski link korunuyor): {key}")
        time.sleep(2)

def background_token_updater():
    print("[*] Arka plan token güncelleyici başlatıldı.")
    update_all_tokens()
    while True:
        time.sleep(900)
        update_all_tokens()

@app.route("/restart")
def restart_tokens():
    try:
        threading.Thread(target=update_all_tokens, daemon=True).start()
        return "<h3>[✓] Tüm kanal tokenleri ve cache verileri arka planda yenilenmeye başlandı! Sunucu aktif.</h3>", 200
    except Exception as e:
        return f"Yenileme sırasında hata oluştu: {str(e)}", 500

@app.route("/playlist.m3u8")
def global_playlist():
    host_root = request.host_url.rstrip("/")
    m3u8_content = "#EXTM3U\n"
    
    for channel_name in CHANNELS_DATA:
        m3u8_content += f"#EXTINF:-1,{channel_name.upper()}\n"
        m3u8_content += f"{host_root}/{channel_name}/master.m3u8\n"
        
    response = Response(m3u8_content, mimetype="application/vnd.apple.mpegurl")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Connection"] = "keep-alive"
    return response

@app.route("/<channel_name>/master.m3u8")
def proxy_master(channel_name):
    try:
        if channel_name not in CHANNELS_DATA:
            return "Kanal bulunamadı", 404

        channel_info = CHANNELS_DATA[channel_name]
        chunks_url = channel_info["cached_chunks_url"]
        referer = channel_info["referer"]

        if not chunks_url:
            chunks_url = fetch_token_for_channel(channel_name)
            if chunks_url:
                channel_info["cached_chunks_url"] = chunks_url

        if not chunks_url:
            return "Kanal linki henüz hazır değil, lütfen birazdan tekrar deneyin.", 503
        
        target_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": referer,
            "Origin": referer.rstrip("/"),
            "Connection": "keep-alive",
        }

        resp = global_session.get(chunks_url, headers=target_headers, timeout=15)
        resp.raise_for_status()

        playlist_content = resp.text
        base_url = re.sub(r"[^/]+$", "", chunks_url)
        host_root = request.host_url.rstrip("/")

        new_lines = []
        for line in playlist_content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if line.startswith("http://") or line.startswith("https://"):
                    full_url = line
                else:
                    full_url = base_url + line
                
                # Parça adını (örn: l_xxx.ts) ayıkla ve temiz URL formatına dönüştür
                parsed_ts = urlparse(full_url)
                ts_filename = parsed_ts.path.split("/")[-1]
                
                # Temiz yol: /<channel_name>/<segment_name>.ts
                proxied_url = f"{host_root}/{channel_name}/{ts_filename}"
                new_lines.append(proxied_url)
            else:
                new_lines.append(line)

        updated_playlist = "\n".join(new_lines)
        
        response = Response(updated_playlist, mimetype="application/vnd.apple.mpegurl")
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Connection"] = "keep-alive"
        return response

    except Exception as e:
        return f"Playlist alınırken hata oluştu: {str(e)}", 500


@app.route("/<channel_name>/<path:subpath>")
def proxy_segment(channel_name, subpath):
    if channel_name not in CHANNELS_DATA:
        return "Kanal bulunamadı", 404

    channel_info = CHANNELS_DATA[channel_name]
    cached_url = channel_info.get("cached_chunks_url")
    referer = channel_info.get("referer")

    if not cached_url:
        return "Token hazır değil", 503

    # CDN temel kök dizinini al (örn: https://.../hls/elhayattv)
    base_cdn_root = cached_url.rsplit('/', 1)[0]
    target_url = f"{base_cdn_root}/{subpath}"

    target_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
        "Origin": referer.rstrip("/"),
        "Connection": "keep-alive",
    }

    if "Range" in request.headers:
        target_headers["Range"] = request.headers.get("Range")

    try:
        req = global_session.get(target_url, headers=target_headers, stream=True, timeout=15)
        
        content_type = req.headers.get("Content-Type", "application/octet-stream")

        # Live TV 3.2 ve ExoPlayer için katı MIME-type eşleştirmesi
        if target_url.endswith(".ts") or "video/mp2t" in content_type:
            content_type = "video/mp2t"
        elif target_url.endswith(".m4s"):
            content_type = "video/iso.segment"

        response = Response(req.iter_content(chunk_size=131072), status=req.status_code, mimetype=content_type)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Connection"] = "keep-alive"
        
        if "Content-Range" in req.headers:
            response.headers["Content-Range"] = req.headers.get("Content-Range")
        if "Content-Length" in req.headers:
            response.headers["Content-Length"] = req.headers.get("Content-Length")

        return response

    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    t = threading.Thread(target=background_token_updater, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
