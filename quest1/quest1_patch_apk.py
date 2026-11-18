"""Patch the HL2Quest 1.02 APK so it runs on the original Quest 1 (Snapdragon 835, Horizon OS v50).
Three fixes, each documented next to its code below:
  1. Launcher menu: launcher.js uses ?. and ?? (Chrome 80+); the Quest 1 WebView is Chrome 74, so the
     whole script fails to parse and the menu is blank. The 6 edits rewrite them as plain checks.
  2. OpenXR: libsourcevr.so asks for API 1.1; the Quest 1 runtime only accepts 1.0.
  3. DXVK: libdxvk_d3d9.so requires a Vulkan extension and features the Adreno 540 driver lacks.
  4. DX level: libshaderapidx9.so tests sRGB support on DXT1, which the Quest 1 can't read at all.
  5. Manifest: the game activity needs com.oculus.intent.category.VR or v50 shows it as a flat panel.
Every other APK entry is copied unchanged (raw bytes), and the APK is v1-signed with our own key
(targetSdk 24, so v1 is enough). Different key from the official APK: uninstall that first.
Usage: python3 quest1_patch_apk.py hl2quest-vr-1.02.apk hl2quest-vr-1.02-quest1.apk WORKDIR
WORKDIR holds key.pem/cert.pem (made on first run; reuse it so later builds install with -r)."""
import sys, zipfile, struct, zlib, hashlib, base64, subprocess, os
src, dst, wd = sys.argv[1:4]
os.makedirs(wd, exist_ok=True)
JS = "assets/hl2quest_launcher_web/launcher.js"
EDITS = [
 ("String(s??'')", "String(s==null?'':s)"),
 ("state?.russian!==false?ru:(state?.translations?.[en]??en)",
  "(!state||state.russian!==false)?ru:((state.translations&&state.translations[en]!=null)?state.translations[en]:en)"),
 ("latest?.name||", "(latest&&latest.name)||"),
 ("latest?.date||", "(latest&&latest.date)||"),
 ("rows[0]?.id||''", "(rows[0]&&rows[0].id)||''"),
 ("control?.enabled===false", "(control!=null&&control.enabled===false)"),
]
# libsourcevr.so asks OpenXR for API 1.1.53; the Quest 1 runtime (v50) only accepts 1.0, so
# xrCreateInstance fails with XR_ERROR_API_VERSION_UNSUPPORTED (-4). Ask for 1.0.0 instead:
#   mov x11,#0x35 -> mov x11,#0      movk x11,#1,lsl#32 -> nop     (movk x11,#1,lsl#48 stays)
SO = "lib/arm64-v8a/libsourcevr.so"
SO_OLD = struct.pack("<4I", 0xd28006ab, 0xf2c0002b, 0x5280002a, 0xf2e0002b)
SO_NEW = struct.pack("<4I", 0xd280000b, 0xd503201f, 0x5280002a, 0xf2e0002b)
# libdxvk_d3d9.so (DXVK 1.9.2) asks for things the Quest 1's Adreno 540 driver (512.555, Vulkan 1.1.128) lacks.
# Each edit: (file offset, old word, new word). Checked against the Quest 1 vkjson dump.
#  DxvkAdapter::createDevice: VK_KHR_image_format_list Required -> Optional (str d2 -> str d0; d0={Optional,0})
#  D3D9DeviceEx::GetDeviceFeatures: stop forcing features the Quest 1 reports as 0 (struct is memset 0 first):
#    geometryShader          str w9,[x19,#0x20]       -> nop
#    multiViewport           stp w9,w22,[x19,#0x58]   -> str w22,[x19,#0x5c]  (keeps samplerAnisotropy)
#    textureCompressionBC +  stp d0,d1,[x19,#0x68]    -> str d1,[x19,#0x70]   (keeps pipelineStatisticsQuery,
#    occlusionQueryPrecise                                                       vertexPipelineStoresAndAtomics)
DX = "lib/arm64-v8a/libdxvk_d3d9.so"
DX_EDITS = [(0x1726e0, 0xfd01d7e2, 0xfd01d7e0),
            (0x118dc0, 0xb9002269, 0xd503201f),
            (0x118dd4, 0x290b5a69, 0xb9005e76),
            (0x118df8, 0x6d068660, 0xfd003a61)]
def patch_dx(data):
    d = bytearray(data)
    for off, old, new in DX_EDITS:
        cur = struct.unpack_from("<I", d, off)[0]
        assert cur == old, ("dxvk word mismatch", hex(off), hex(cur))
        struct.pack_into("<I", d, off, new)
    return bytes(d)
# libshaderapidx9.so: Source decides "supports sRGB" by asking for sRGB reads on DXT1 textures, and dx90
# needs sRGB (max DX level = 90 only with VS2.0 + PS2.0 + sRGB; caps bits 3, 6, 21). The Adreno 540 has
# no DXT/BC at all, so the answer is no, the max DX level drops to 80/81, stdshader_dx9 is never loaded
# and every material fails ("unknown shader UnlitGeneric"). Ask the same question about A8R8G8B8 instead,
# which the Quest 1 does support with sRGB + linear filtering (the follow-up A8R8G8B8 check is unchanged).
#   mov w6,#0x5844 ; movk w6,#0x3154,lsl#16  ('DXT1')  ->  mov w6,#0x15 (D3DFMT_A8R8G8B8) ; nop
SA = "lib/arm64-v8a/libshaderapidx9.so"
SA_EDITS = [(0x8c3c0, 0x528b0886, 0x528002a6),
            (0x8c3e4, 0x72a62a86, 0xd503201f)]
def patch_words(data, edits, what):
    d = bytearray(data)
    for off, old, new in edits:
        cur = struct.unpack_from("<I", d, off)[0]
        assert cur == old, (what + " word mismatch", hex(off), hex(cur))
        struct.pack_into("<I", d, off, new)
    return bytes(d)
# AndroidManifest.xml: SDLActivity (the game) is marked VR only with the OpenXR category IMMERSIVE_HMD,
# which newer Horizon OS honours but the Quest 1's v50 doesn't: it opens the game as a flat 2D panel
# ("launching as reprojected android app") and the XR session never becomes visible (black box).
# v50 needs com.oculus.intent.category.VR (same fix TPVR needed). Swap the string in the manifest's
# string pool; Quest 2/3 accept com.oculus.intent.category.VR too.
AMF = "AndroidManifest.xml"
MF_OLD, MF_NEW = "org.khronos.openxr.intent.category.IMMERSIVE_HMD", "com.oculus.intent.category.VR"
def patch_manifest(d):
    """Replace one string in the binary manifest's UTF-16 string pool (no styles)."""
    assert d[:4] == b"\x03\x00\x08\x00"
    t, hs, sz = struct.unpack_from("<HHI", d, 8)
    cnt, stc, flags, ss, sts = struct.unpack_from("<IIIII", d, 16)
    assert t == 1 and hs == 28 and stc == 0 and not (flags & 0x100), "unexpected string pool"
    offs = struct.unpack_from("<%dI" % cnt, d, 8 + hs)
    strs = []
    for o in offs:
        q = 8 + ss + o; n = struct.unpack_from("<H", d, q)[0]; q += 2
        if n & 0x8000: n = ((n & 0x7fff) << 16) | struct.unpack_from("<H", d, q)[0]; q += 2
        strs.append(d[q:q + 2 * n].decode("utf-16-le"))
    assert strs.count(MF_OLD) == 1, ("manifest string not found exactly once", strs.count(MF_OLD))
    strs[strs.index(MF_OLD)] = MF_NEW
    body = b""; noffs = []
    for s_ in strs:
        noffs.append(len(body)); e = s_.encode("utf-16-le"); n = len(e) // 2
        assert n < 0x8000
        body += struct.pack("<H", n) + e + b"\0\0"
    body += b"\0" * (-len(body) % 4)
    nss = hs + 4 * cnt
    pool = struct.pack("<HHIIIIII", 1, hs, nss + len(body), cnt, 0, flags, nss, 0) + struct.pack("<%dI" % cnt, *noffs) + body
    out = d[:8] + pool + d[8 + sz:]
    return out[:4] + struct.pack("<I", len(out)) + out[8:]
def patch_so(data):
    assert data.count(SO_OLD) == 1, ("apiVersion code not found exactly once", data.count(SO_OLD))
    return data.replace(SO_OLD, SO_NEW)
def patch_js(data):
    s = data.decode("utf-8")
    for old, new in EDITS:
        assert s.count(old) == 1, ("expected exactly one", old, s.count(old))
        s = s.replace(old, new)
    assert "?." not in s.replace("?.5", "") or True
    left = [i for i in range(len(s)-1) if s[i] == "?" and s[i+1] in ".?" and not s[i+2:i+3].isdigit()]
    assert not left, ("leftover ?. or ?? at", left[:5])
    return s.encode("utf-8")
zin = zipfile.ZipFile(src)
raw = open(src, "rb")
def is_sig(n):
    return n == "META-INF/MANIFEST.MF" or (n.startswith("META-INF/") and n.upper().rsplit("/",1)[-1].endswith((".SF",".RSA",".EC",".DSA")))
infos = [i for i in zin.infolist() if not is_sig(i.filename)]
def b64(d): return base64.b64encode(hashlib.sha256(d).digest()).decode()
newjs = None; newso = None; newdx = None; newsa = None; newmf = None; sections = []
for i in infos:
    if i.filename == JS:
        newjs = patch_js(zin.read(i)); d = b64(newjs)
    elif i.filename == SO:
        newso = patch_so(zin.read(i)); d = b64(newso)
    elif i.filename == DX:
        newdx = patch_dx(zin.read(i)); d = b64(newdx)
    elif i.filename == AMF:
        newmf = patch_manifest(zin.read(i)); d = b64(newmf)
    elif i.filename == SA:
        newsa = patch_words(zin.read(i), SA_EDITS, "shaderapidx9"); d = b64(newsa)
    else:
        h = hashlib.sha256()
        with zin.open(i) as f:
            for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
        d = base64.b64encode(h.digest()).decode()
    sections.append((i.filename, f"Name: {i.filename}\r\nSHA-256-Digest: {d}\r\n\r\n"))
assert newjs is not None, "launcher.js not found"
assert newso is not None, "libsourcevr.so not found"
assert newdx is not None, "libdxvk_d3d9.so not found"
assert newsa is not None, "libshaderapidx9.so not found"
assert newmf is not None, "AndroidManifest.xml not found"
mf_main = "Manifest-Version: 1.0\r\nCreated-By: quest1-patcher\r\n\r\n"
MF = (mf_main + "".join(s for _, s in sections)).encode()
SF = ("Signature-Version: 1.0\r\nCreated-By: quest1-patcher\r\n"
      f"SHA-256-Digest-Manifest: {b64(MF)}\r\n"
      f"SHA-256-Digest-Manifest-Main-Attributes: {b64(mf_main.encode())}\r\n\r\n" +
      "".join(f"Name: {n}\r\nSHA-256-Digest: {b64(s.encode())}\r\n\r\n" for n, s in sections)).encode()
key, crt = os.path.join(wd, "key.pem"), os.path.join(wd, "cert.pem")
if not os.path.exists(key):
    subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-keyout",key,"-out",crt,
                    "-days","10000","-subj","/CN=Quest1 Patch"], check=True, capture_output=True)
sfp, rsap = os.path.join(wd, "CERT.SF"), os.path.join(wd, "CERT.RSA")
open(sfp, "wb").write(SF)
subprocess.run(["openssl","smime","-sign","-binary","-noattr","-md","sha256","-outform","DER",
                "-signer",crt,"-inkey",key,"-in",sfp,"-out",rsap], check=True)
now = (2026,1,1,0,0,0)
def dostime(dt): return ((dt[0]-1980)<<9)|(dt[1]<<5)|dt[2], (dt[3]<<11)|(dt[4]<<5)|(dt[5]//2)
def raw_data(i):
    raw.seek(i.header_offset); h = raw.read(30)
    nlen, xlen = struct.unpack_from("<HH", h, 26)
    raw.seek(i.header_offset + 30 + nlen + xlen)
    return i.compress_size
tmp = dst + ".part"
with open(tmp, "wb") as out:
    cdir = []
    def put(name, method, crc, csize, usize, dt, writer):
        nm = name.encode(); off = out.tell()
        pad = (-(off + 30 + len(nm))) % (4096 if name.endswith(".so") else 4) if method == 0 else 0
        dd, tt = dostime(dt)
        out.write(struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, 0x800, method, tt, dd, crc, csize, usize, len(nm), pad))
        out.write(nm + b"\0" * pad); writer()
        cdir.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014b50, 20, 20, 0x800, method, tt, dd, crc, csize, usize, len(nm), 0, 0, 0, 0, 0, off) + nm)
    def put_bytes(name, data, dt, stored=False):
        comp = data if stored else (lambda c: c.compress(data) + c.flush())(zlib.compressobj(9, zlib.DEFLATED, -15))
        put(name, 0 if stored else 8, zlib.crc32(data) & 0xffffffff, len(comp), len(data), dt, lambda: out.write(comp))
    put_bytes("META-INF/MANIFEST.MF", MF, now); put_bytes("META-INF/CERT.SF", SF, now)
    put_bytes("META-INF/CERT.RSA", open(rsap, "rb").read(), now)
    for i in infos:
        if i.filename == JS:
            put_bytes(JS, newjs, i.date_time, stored=(i.compress_type == 0)); continue
        if i.filename == SO:
            put_bytes(SO, newso, i.date_time, stored=(i.compress_type == 0)); continue
        if i.filename == DX:
            put_bytes(DX, newdx, i.date_time, stored=(i.compress_type == 0)); continue
        if i.filename == AMF:
            put_bytes(AMF, newmf, i.date_time, stored=(i.compress_type == 0)); continue
        if i.filename == SA:
            put_bytes(SA, newsa, i.date_time, stored=(i.compress_type == 0)); continue
        assert i.compress_type in (0, 8), (i.filename, i.compress_type)
        def copy(i=i):
            n = raw_data(i)
            while n:
                c = raw.read(min(n, 1 << 20)); out.write(c); n -= len(c)
        put(i.filename, i.compress_type, i.CRC, i.compress_size, i.file_size, i.date_time, copy)
    cd = out.tell(); out.write(b"".join(cdir)); end = out.tell()
    out.write(struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, len(cdir), len(cdir), end-cd, cd, 0))
os.replace(tmp, dst)
# self-check: every entry reads back with a good CRC, and launcher.js is the patched one
z = zipfile.ZipFile(dst); bad = z.testzip()
assert bad is None, ("CRC error in", bad)
assert z.read(JS) == newjs and z.read(SO) == newso and z.read(DX) == newdx and z.read(SA) == newsa and z.read(AMF) == newmf
print("OK wrote", dst, len(cdir), "entries")
