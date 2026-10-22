"""Render narration with Gemini TTS. Runs on a fleet runner; the key comes from the env and is never printed.

env: GEMINI_API_KEY
args: --text-b64 B64 --voices A,B --model M --style S --out DIR [--list-models]
Writes DIR/<voice>.wav (24 kHz mono 16-bit) and DIR/report.json.
"""
import argparse, base64, json, os, sys, time, urllib.error, urllib.request, wave

API = "https://generativelanguage.googleapis.com/v1beta"
KEY = os.environ.get("GEMINI_API_KEY", "")


def call(method, path, body=None):
    req = urllib.request.Request(f"{API}/{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "x-goog-api-key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:  # body only: never the request headers
        return e.code, {"error": e.read().decode(errors="replace")[:800]}


ap = argparse.ArgumentParser()
ap.add_argument("--text-b64", default="")
ap.add_argument("--voices", default="")
ap.add_argument("--model", default="")
ap.add_argument("--style", default="")
ap.add_argument("--out", default="out")
ap.add_argument("--list-models", action="store_true")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
if not KEY:
    sys.exit("GEMINI_API_KEY missing")

report = {}
if a.list_models or not a.model:
    names, token = [], ""
    while True:
        st, js = call("GET", "models?pageSize=200" + (f"&pageToken={token}" if token else ""))
        if st != 200:
            print("list models failed", st, js); break
        names += [m["name"] for m in js.get("models", []) if "tts" in m["name"].lower() or "AUDIO" in str(m.get("supportedGenerationMethods", ""))]
        token = js.get("nextPageToken", "")
        if not token:
            break
    print("TTS-capable models:", names)
    report["models"] = names

text = base64.b64decode(a.text_b64).decode("utf-8") if a.text_b64 else ""
if text and a.model:
    style = a.style.strip()
    lang = ""
    if style.startswith("LANG:"):  # e.g. "LANG:en-IN" or "LANG:en-IN | direction text"
        lang, _, style = style[5:].partition("|")
        lang, style = lang.strip(), style.strip()
    sys_style = style[4:].strip() if style.startswith("SYS:") else ""
    # documented Gemini TTS pattern: a one-line direction ending in ':' then the words to speak
    prompt = text if (sys_style or not style) else f"{style.rstrip(':')}: {text}"
    report["takes"] = {}
    for voice in [v.strip() for v in a.voices.split(",") if v.strip()]:
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        if sys_style:
            body["systemInstruction"] = {"parts": [{"text": sys_style}]}
        if lang:
            body["generationConfig"]["speechConfig"]["languageCode"] = lang
        for attempt in range(3):
            t0 = time.time()
            st, js = call("POST", f"models/{a.model}:generateContent", body)
            if st == 200:
                break
            print(voice, "attempt", attempt + 1, "HTTP", st, js.get("error", "")[:300])
            time.sleep(10 * (attempt + 1))
        if st != 200:
            report["takes"][voice] = {"error": st}
            continue
        part = js["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
        with wave.open(os.path.join(a.out, f"{voice}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
        secs = len(pcm) / 2 / 24000
        report["takes"][voice] = {"seconds": round(secs, 2), "mime": part.get("mimeType"), "elapsed": round(time.time() - t0, 1),
                                  "usage": js.get("usageMetadata", {})}
        print(voice, f"{secs:.1f}s audio", part.get("mimeType"), js.get("usageMetadata", {}))
json.dump(report, open(os.path.join(a.out, "report.json"), "w"), indent=1)
