#!/usr/bin/env python3
"""Página "Anúncios ativos" da Maia para os corretores.

Lê no Meta os anúncios com entrega ATIVA, junta por criativo (o mesmo vídeo rodando
em vários públicos vira um card só) e gera a página em <saida>/ com as capas e banners.
Publicada em https://beatriz-moraes082.github.io/maia-dash/anuncios/ pelo .github/workflows/anuncios.yml deste repo
(público, então o GitHub Actions roda de graça). Pasta com "_" no nome: o Pages não publica.

Link de cada card: post do Instagram do criativo (abre sem login). O link de prévia
do Meta (fb.me) pede login, então NÃO serve. Sem post no Instagram, vale o link do
Drive configurado em anuncios_config.json; sem nada, o card diz que ainda não tem link.

Uso:
    python3 anuncios_ativos.py <saida>              # gera a página
    python3 anuncios_ativos.py <saida> --compara <pasta publicada>
        # sai com código 3 se a lista de anúncios não mudou (não precisa publicar)

Env: MAIA_META_TOKEN, MAIA_AD_ACCOUNT (act_...).
"""

import collections
import html
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "v21.0"
AQUI = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ.get("MAIA_META_TOKEN", "")
CONTA = os.environ.get("MAIA_AD_ACCOUNT", "act_919375583445318")
if not CONTA.startswith("act_"):
    CONTA = "act_" + CONTA
BRT = timezone(timedelta(hours=-3))


def get(path, **params):
    params["access_token"] = TOKEN
    url = path if path.startswith("http") else f"https://graph.facebook.com/{API}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"Meta HTTP {e.code} em {path}: {msg}")


def baixar(url, destino, lado_max=1000):
    with urllib.request.urlopen(url, timeout=60) as r:
        dados = r.read()
    with open(destino, "wb") as f:
        f.write(dados)
    try:  # reduz se o Pillow existir; sem ele fica o original
        from PIL import Image
        im = Image.open(destino)
        im.thumbnail((lado_max, lado_max))
        im.convert("RGB").save(destino, "JPEG", quality=82, optimize=True)
    except ImportError:
        pass


# ---------------- coleta ----------------
def anuncios_ativos():
    campos = ("id,name,campaign{name},adset{name},creative{id,instagram_permalink_url,"
              "object_story_spec,asset_feed_spec,image_hash,video_id}")
    url = None
    out = []
    params = {"fields": campos, "effective_status": json.dumps(["ACTIVE"]), "limit": 200}
    d = get(f"{CONTA}/ads", **params)
    while True:
        out += d.get("data", [])
        url = d.get("paging", {}).get("next")
        if not url:
            break
        d = get(url)
    return out


def midia(criativo):
    """(videos, hashes de imagem) do criativo."""
    oss = criativo.get("object_story_spec") or {}
    afs = criativo.get("asset_feed_spec") or {}
    vids = []
    v = criativo.get("video_id") or (oss.get("video_data") or {}).get("video_id")
    if v:
        vids.append(v)
    vids += [x.get("video_id") for x in afs.get("videos", []) if x.get("video_id")]
    hashes = [x.get("hash") for x in afs.get("images", []) if x.get("hash")]
    for h in [criativo.get("image_hash"), (oss.get("link_data") or {}).get("image_hash"),
              (oss.get("photo_data") or {}).get("image_hash")]:
        if h and h not in hashes:
            hashes.append(h)
    return list(dict.fromkeys(vids)), hashes


# ---------------- regras de nome ----------------
def grupo(nome_campanha, cfg):
    partes = [p.strip() for p in nome_campanha.split("|")]
    if "topo" in partes[0].lower():
        return cfg.get("grupo_alcance", "Vídeos de alcance")
    g = partes[2] if len(partes) > 2 and partes[2] else nome_campanha
    g = re.sub(r"\s*#TP\s*$", "", g).strip()
    return cfg.get("nome_grupo", {}).get(g, g)


def rotulo(nome_anuncio, grupo_nome, tipo, n, cfg):
    if nome_anuncio in cfg.get("rotulos", {}):
        return cfg["rotulos"][nome_anuncio]
    m = re.match(r"^(VD|BN|CR|IMG)\s*\d+\s*[-|]?\s*(.*)$", nome_anuncio, re.I)
    resto = (m.group(2) if m else nome_anuncio).strip(" -|")
    if not resto or resto.lower() == grupo_nome.lower():
        return f"{grupo_nome} · {'vídeo' if tipo == 'video' else 'banner'} {n}"
    return resto[0].upper() + resto[1:]


def codigo(nome_anuncio):
    m = re.match(r"^((VD|BN|CR|IMG)\s*\d+)", nome_anuncio, re.I)
    return m.group(1).upper().replace(" ", "") if m else ""


# ---------------- montagem ----------------
def montar(saida):
    cfg = json.load(open(os.path.join(AQUI, "anuncios_config.json"), encoding="utf-8"))
    ads = anuncios_ativos()
    itens = collections.OrderedDict()
    videos_info = {}
    for a in ads:
        cr = a.get("creative") or {}
        vids, hashes = midia(cr)
        g = grupo(a["campaign"]["name"], cfg)
        if vids:
            vid = vids[0]
            if vid not in videos_info:
                videos_info[vid] = get(vid, fields="title,length")
            vi = videos_info[vid]
            chave = (g, "v", vi.get("title") or a["name"], round(float(vi.get("length") or 0)))
            tipo = "video"
        elif hashes:
            chave = (g, "i", tuple(sorted(hashes)))
            tipo = "imagem"
        else:
            print(f"aviso: anúncio {a['id']} ({a['name']}) sem vídeo nem imagem, fica fora", file=sys.stderr)
            continue
        it = itens.get(chave)
        if not it:
            it = itens[chave] = {"grupo": g, "tipo": tipo, "ads": [], "ig": None, "nome": a["name"],
                                 "vid": vids[0] if vids else None, "hashes": hashes,
                                 "titulo_video": videos_info[vids[0]].get("title") if vids else None,
                                 "dur": float(videos_info[vids[0]].get("length") or 0) if vids else None}
        it["ads"].append(a["id"])
        if not it["ig"] and cr.get("instagram_permalink_url"):
            it["ig"] = cr["instagram_permalink_url"]

    # grupos: ordem do config, depois os novos por tamanho, alcance no fim
    alcance = cfg.get("grupo_alcance", "Vídeos de alcance")
    por_grupo = collections.OrderedDict()
    for it in itens.values():
        por_grupo.setdefault(it["grupo"], []).append(it)
    ordem = [g for g in cfg.get("ordem_grupos", []) if g in por_grupo]
    novos = sorted([g for g in por_grupo if g not in ordem and g != alcance], key=lambda g: -len(por_grupo[g]))
    ordem = [g for g in ordem if g != alcance] + novos + ([alcance] if alcance in por_grupo else [])

    # rótulos (numeração por tipo dentro do grupo) e mídia
    if os.path.isdir(saida):
        shutil.rmtree(saida)
    os.makedirs(saida)
    for f in ("trilha-logo.png", "trilha-logo-escura.png"):
        shutil.copy(os.path.join(AQUI, "anuncios_assets", f), saida)
    for g in ordem:
        cont = collections.Counter()
        for it in por_grupo[g]:
            cont[it["tipo"]] += 1
            it["rotulo"] = rotulo(it["nome"], g, it["tipo"], cont[it["tipo"]], cfg)
            it["codigo"] = codigo(it["nome"])
            if it["tipo"] == "video":
                th = get(f"{it['vid']}/thumbnails").get("data", [])
                pref = [t for t in th if t.get("is_preferred")] or th
                it["arquivo"] = f"capa-{it['vid']}.jpg"
                if pref:
                    baixar(pref[0]["uri"], os.path.join(saida, it["arquivo"]), 900)
                else:
                    it["arquivo"] = None
                it["link"] = it["ig"] or cfg.get("drive_por_video", {}).get(it["titulo_video"] or "") \
                    or cfg.get("drive_por_anuncio", {}).get(it["nome"])
                it["link_tipo"] = "Instagram" if it["ig"] else ("Drive" if it["link"] else None)
            else:
                imgs = get(f"{CONTA}/adimages", hashes=json.dumps(it["hashes"]), fields="hash,url,width,height").get("data", [])
                # formato mais perto de 4:5 (o do feed)
                imgs.sort(key=lambda x: abs(x["width"] / x["height"] - 0.8))
                it["arquivo"] = f"img-{imgs[0]['hash'][:12]}.jpg" if imgs else None
                if imgs:
                    baixar(imgs[0]["url"], os.path.join(saida, it["arquivo"]), 1200)
                it["link"] = it["arquivo"]
                it["link_tipo"] = "imagem"
        # vídeos na ordem do Meta, banners depois, em ordem de nome
        por_grupo[g] = ([x for x in por_grupo[g] if x["tipo"] == "video"]
                        + sorted([x for x in por_grupo[g] if x["tipo"] == "imagem"], key=lambda x: x["rotulo"]))

    estado = [{"grupo": g, "rotulo": it["rotulo"], "link": it["link"], "ads": sorted(it["ads"])}
              for g in ordem for it in por_grupo[g]]
    agora = datetime.now(BRT)
    pagina = render(ordem, por_grupo, cfg, agora)
    with open(os.path.join(saida, "index.html"), "w", encoding="utf-8") as f:
        f.write(pagina)
    with open(os.path.join(saida, "estado.json"), "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)
    print(f"{len(ads)} anúncios ativos → {sum(len(v) for v in por_grupo.values())} criativos em {len(ordem)} grupos",
          file=sys.stderr)
    for g in ordem:
        print(f"  {g}: " + "; ".join(f"{it['rotulo']} ({it['link_tipo'] or 'sem link'})" for it in por_grupo[g]),
              file=sys.stderr)
    return estado


def slug(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def dur(s):
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>'


def render(ordem, por_grupo, cfg, agora):
    e = html.escape
    alcance = cfg.get("grupo_alcance", "Vídeos de alcance")
    nav, secs = [], []
    for g in ordem:
        its = por_grupo[g]
        sid = slug(g)
        nav.append(f'<a href="#{sid}">{e(g)}<span>{len(its)}</span></a>')
        cards = []
        for it in its:
            if it["tipo"] == "imagem":
                media = (f'<a class="thumb" href="{e(it["arquivo"])}" target="_blank" rel="noopener">'
                         f'<img src="{e(it["arquivo"])}" alt="{e(g)}, {e(it["rotulo"])}" loading="lazy"></a>') if it["arquivo"] else '<div class="thumb"></div>'
                meta = "Imagem"
                acao = f'<a class="btn" href="{e(it["arquivo"])}" target="_blank" rel="noopener">Abrir imagem</a>' if it["arquivo"] else ""
            else:
                href = it["link"]
                tag = "a" if href else "div"
                attrs = f' href="{e(href)}" target="_blank" rel="noopener"' if href else ""
                img = f'<img src="{e(it["arquivo"])}" alt="Capa do vídeo {e(it["rotulo"])}" loading="lazy">' if it["arquivo"] else ""
                media = f'<{tag} class="thumb"{attrs}>{img}<span class="play">{PLAY}</span><span class="dur">{dur(it["dur"])}</span></{tag}>'
                meta = f"Vídeo · {dur(it['dur'])}"
                acao = (f'<a class="btn" href="{e(href)}" target="_blank" rel="noopener">Assistir no {it["link_tipo"]}</a>'
                        if href else '<span class="pending">Sem link público por enquanto</span>')
            cod = f'<span class="code">{e(it["codigo"])}</span>' if it["codigo"] else ""
            cards.append(f'<article class="card">{media}<div class="body"><div class="row"><h3>{e(it["rotulo"])}</h3>{cod}</div>'
                         f'<p class="meta">{meta}</p>{acao}</div></article>')
        nota = ('<p class="note">Rodam para gerar visualização, sem formulário. O lead não chega por eles direto.</p>'
                if g == alcance else "")
        n = len(its)
        secs.append(f'<section id="{sid}"><header><h2>{e(g)}</h2><span class="count">{n} {"criativo" if n == 1 else "criativos"}</span></header>'
                    f'{nota}<div class="grid">{"".join(cards)}</div></section>')
    if not secs:
        secs.append('<section><p class="note">Nenhum anúncio ativo neste momento.</p></section>')
    ondas = "".join(
        f'<path d="M0 {120 + 70 * i} C 150 {80 + 70 * i}, 300 {170 + 70 * i}, 600 {110 + 70 * i}" fill="none" '
        f'stroke="{"var(--signal)" if i == 3 else "var(--cream)"}" stroke-opacity="{0.9 if i == 3 else round(0.05 + 0.012 * i, 3)}" '
        f'stroke-width="{2 if i == 3 else 1}"/>' for i in range(9))
    quando = agora.strftime("%d/%m/%Y, %Hh%M")
    tpl = open(os.path.join(AQUI, "anuncios_assets", "modelo.html"), encoding="utf-8").read()
    return (tpl.replace("{{ONDAS}}", ondas).replace("{{NAV}}", "".join(nav))
               .replace("{{SECOES}}", "".join(secs)).replace("{{QUANDO}}", quando))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if not TOKEN:
        raise SystemExit("falta MAIA_META_TOKEN")
    saida = sys.argv[1]
    estado = montar(saida)
    if "--compara" in sys.argv:
        pub = sys.argv[sys.argv.index("--compara") + 1]
        try:
            antigo = json.load(open(os.path.join(pub, "estado.json"), encoding="utf-8"))
        except (OSError, ValueError):
            antigo = None
        if antigo == estado:
            print("lista igual à publicada: nada a publicar", file=sys.stderr)
            sys.exit(3)
        print("lista mudou: publicar", file=sys.stderr)


if __name__ == "__main__":
    main()
