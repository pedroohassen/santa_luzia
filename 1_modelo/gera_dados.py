# gera_dados.py
# Le a planilha base e escreve o arquivo .dat do modelo_aps.mod para um cenario.
import math
import os
import pandas as pd

PASTA = os.path.dirname(os.path.abspath(__file__))


def _pasta(*opcoes):
    """Primeira pasta que existe: os scripts rodam com dados/ e resultados/ ao lado ou com ../2_dados e ../3_resultados."""
    op = [os.path.normpath(os.path.join(PASTA, o)) for o in opcoes]
    return next((o for o in op if os.path.isdir(o)), op[0])


DADOS = _pasta("dados", "../2_dados")
RESULTADOS = _pasta("resultados", "../3_resultados")
DAT = _pasta("dat", "dados/glpk")                                    # arquivos .dat do GLPK
PLANILHA = os.path.join(DADOS, "base_dados_saude_santa_luzia.xlsx")
PORTE = os.path.join(DADOS, "porte_ubs_cnes.xlsx")          # porte (no maximo de eSF) de cada UBS, por CNES
REDE = os.path.join(DADOS, "distancias_rede.csv")          # distancias pela rede de ruas (distancias_rede.py)
REDE_XLSX = os.path.join(DADOS, "matriz_distancias_rede.xlsx")  # a mesma matriz, em Excel (aba "Matriz completa (km)")
R_TERRA = 6371.0

# Cenario principal (C1 - Ideal). Cada chave pode ser trocada em resolver.py (cenarios e sensibilidade).
CENARIO_BASE = {
    "nome": "C1",
    "pop_ref": "ms",       # "ms" = 230.382 (referencia e-Gestor 2025) | "censo" = 219.132 (IBGE 2022)
    "cap": 3500,           # pessoas por eSF: Nota Tecnica n. 2/2025-SAPS/MS (cobertura potencial da APS)
    "dmax_urb": 3.0,       # km, setor em "Area urbanizada de cidade ou vila" (IBGE 2022)
    "dmax_nurb": 5.0,      # km, demais setores (area nao urbanizada, nucleo urbano isolado, rural)
    "meta": 1.0,           # fracao minima da populacao com cobertura efetiva (C3: 0,99)
    "pmax": 5,             # maior porte do Novo PAC (UBS V = 5 eSF)
    "candidatos": "todos", # "todos" = centro de qualquer setor | numero X = setores a mais de X km de uma UBS
    "eap": False,          # True = permite UBS nova com eAP 20h/30h (cenario de sensibilidade)
    "anos_crescimento": 0, # projeta a populacao N anos alem de 2025, com a taxa anual Censo 2022 -> MS 2025
    "distancia": "rede",   # "rede" = trajeto pela rede de ruas (OpenStreetMap, a pe) | "reta" = linha reta (Haversine)
    "fator_rota": 1.0,     # multiplicador aplicado a distancia (1,0 = sem ajuste)
    "ampliacao": True,     # True = UBS existentes podem receber eSF novas ate o porte (planilha CNES-Porte)
    "fracionado": False,   # True = permite dividir um setor entre unidades (so para comparacao)
}

_CACHE_REDE = {}


def le_rede():
    """Distancias pela rede de ruas: {(origem, destino): km}, ids 'S<setor>' e 'E<linha da planilha>'."""
    if not _CACHE_REDE:
        if os.path.exists(REDE):
            r = pd.read_csv(REDE, sep=";", dtype={"origem": str, "destino": str})
            _CACHE_REDE.update(zip(zip(r.origem, r.destino), r.dist_rede_km))
        elif os.path.exists(REDE_XLSX):
            m = pd.read_excel(REDE_XLSX, sheet_name="Matriz completa (km)", index_col=0)
            for o, linha in zip(m.index.astype(str), m.to_numpy()):
                _CACHE_REDE.update(((o, d), v) for d, v in zip(m.columns, linha))
        else:
            raise SystemExit("Falta matriz_distancias_rede.xlsx: rode antes 'python distancias_rede.py'.")
    return _CACHE_REDE


def le_porte():
    """CNES -> porte (numero maximo de eSF que a UBS comporta)."""
    p = pd.read_excel(PORTE, header=1).dropna(axis=1, how="all")
    return {int(c): int(v) for c, v in zip(p["CO_CNES"], p["PORTE"])}


def hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_TERRA * math.asin(math.sqrt(a))


def le_planilha():
    est = pd.read_excel(PLANILHA, sheet_name="Estabelecimentos")
    setores = pd.read_excel(PLANILHA, sheet_name="Setores")
    ind = pd.read_excel(PLANILHA, sheet_name="Indicadores").set_index("indicador")["valor"]
    return est, setores, ind


def monta_instancia(cfg):
    """Devolve um dicionario com conjuntos, parametros e coordenadas do cenario."""
    est, setores, ind = le_planilha()

    ubs = est[(est["nivel_modelo"] == 1) & (est["situacao"] == "Ativo")]
    apoios = est[est["situacao"] == "Ativo (sem CNES)"]

    pontos = {}  # id -> dict(nome, lat, lon, tipo, distrito, rede)
    eq, porte, amp = {}, {}, {}
    portes_cnes = le_porte()
    for k, r in ubs.iterrows():
        uid = f"UBS_{int(r['cnes'])}"
        pontos[uid] = dict(nome=r["nome"], lat=r["latitude"], lon=r["longitude"], tipo="UBS", distrito=r["distrito"],
                           rede=f"E{k}")
        eq[uid] = int(r["n_esf"])
        # UBS sem porte na planilha (Cotegipe): considerada sem espaco para novas eSF (porte = eSF atuais)
        porte[uid] = max(eq[uid], portes_cnes.get(int(r["cnes"]), eq[uid]))
        amp[uid] = porte[uid] - eq[uid] if cfg.get("ampliacao", True) else 0
    U = list(eq)

    A = []
    for k, (lin, r) in enumerate(apoios.iterrows(), start=1):
        aid = f"APOIO_{k}"
        pontos[aid] = dict(nome=r["nome"], lat=r["latitude"], lon=r["longitude"], tipo="APOIO", distrito=r["distrito"],
                           rede=f"E{lin}")
        A.append(aid)
    # UBS responsavel pelo ponto de apoio = UBS existente mais proxima (premissa), pela mesma medida de
    # distancia do cenario (rede de ruas ou linha reta)
    if cfg.get("distancia", "rede") == "rede":
        _r = le_rede()
        mae = {a: min(U, key=lambda u: _r[(pontos[a]["rede"], pontos[u]["rede"])]) for a in A}
    else:
        mae = {a: min(U, key=lambda u: hav(pontos[a]["lat"], pontos[a]["lon"],
                                           pontos[u]["lat"], pontos[u]["lon"])) for a in A}

    # Setores (demanda)
    total_censo = setores["populacao"].sum()
    alvo = ind["populacao_referencia_ms"] if cfg["pop_ref"] == "ms" else total_censo
    fator = float(alvo) / float(total_censo)
    if cfg.get("anos_crescimento"):
        taxa = (float(ind["populacao_referencia_ms"]) / float(total_censo)) ** (1 / 3)  # 2022 -> 2025
        fator *= taxa ** cfg["anos_crescimento"]
    S, pop, nurb, coord_s, area, distrito = [], {}, {}, {}, {}, {}
    for _, s in setores.iterrows():
        sid = str(int(s["cd_setor"]))
        S.append(sid)
        pop[sid] = float(s["populacao"]) * fator
        # IBGE 2022: so a situacao "Area urbanizada de cidade ou vila" usa o raio urbano
        nurb[sid] = 0 if s["situacao"] == "Área urbanizada de cidade ou vila" else 1
        coord_s[sid] = (s["latitude"], s["longitude"])
        area[sid] = float(s["area_km2"])
        distrito[sid] = s["distrito"]

    # Locais candidatos a UBS nova: centro dos setores. "todos" = qualquer setor (sem ampliacao,
    # uma UBS nova pode ser necessaria junto das UBS lotadas, dentro da area ja servida);
    # numero X = so setores a mais de X km de qualquer UBS existente (criterio alternativo, sensibilidade).
    N = []
    for sid in S:
        dmin = min(hav(*coord_s[sid], pontos[u]["lat"], pontos[u]["lon"]) for u in U)
        if cfg["candidatos"] == "todos" or dmin > float(cfg["candidatos"]):
            nid = f"NOVA_{sid[-8:]}"
            pontos[nid] = dict(nome=f"UBS nova (setor {sid})", lat=coord_s[sid][0],
                               lon=coord_s[sid][1], tipo="NOVA", distrito=distrito[sid], setor=sid, rede=f"S{sid}")
            N.append(nid)

    J = U + A + N
    fr = cfg.get("fator_rota", 1.0)
    if cfg.get("distancia", "rede") == "rede":
        rede = le_rede()
        dist = {}
        for i in S:
            for j in J:
                d = rede.get((f"S{i}", pontos[j]["rede"]))
                if d is None:   # par sem caminho na rede (nao ocorre na base atual): linha reta x 1,3
                    d = 1.3 * hav(*coord_s[i], pontos[j]["lat"], pontos[j]["lon"])
                dist[i, j] = fr * d
    else:
        dist = {(i, j): fr * hav(*coord_s[i], pontos[j]["lat"], pontos[j]["lon"]) for i in S for j in J}

    cap = float(cfg["cap"])
    custo_porte = {p: float(ind[f"custo_construcao_ubs_porte_{p}"]) for p in range(1, cfg["pmax"] + 1)}

    # Tipos de UBS nova: porte I..V (1 a 5 eSF, Novo PAC) e, opcionalmente, unidade com eAP
    # (Portaria GM/MS 2.539/2019). Capacidade da eAP pela NT 2/2025-SAPS/MS: 20h = 1.750 e
    # 30h = 2.625 pessoas (50% e 75% da eSF). Premissa: a obra da eAP e a de uma UBS porte I.
    tipos = {f"P{p}": dict(eq=float(p), custo=custo_porte[p], nome=f"UBS porte {p} ({p} eSF)")
             for p in range(1, cfg["pmax"] + 1)}
    if cfg.get("eap"):
        tipos["EAP20"] = dict(eq=0.5, custo=custo_porte[1], nome="Unidade com eAP 20h")
        tipos["EAP30"] = dict(eq=0.75, custo=custo_porte[1], nome="Unidade com eAP 30h")

    return dict(cfg=cfg, S=S, U=U, A=A, N=N, J=J, pop=pop, nurb=nurb, dist=dist, area=area,
                distrito=distrito, eq=eq, porte=porte, amp=amp, mae=mae, cap=cap, custo_porte=custo_porte, tipos=tipos,
                pontos=pontos, coord_s=coord_s, fator=fator)


def escreve_dat(inst, caminho, extras=None):
    """Escreve o .dat. 'extras' = parametros de etapa (ETAPA, OBRIG, LIM_EQ, LIM_INV)."""
    cfg = inst["cfg"]
    dlim = max(cfg["dmax_urb"], cfg["dmax_nurb"])
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(f"/* Dados do modelo_aps.mod - cenario '{cfg['nome']}' (gerado por gera_dados.py) */\n")
        f.write("data;\n\n")
        for nome in ("S", "U", "A", "N", "T"):
            elems = list(inst["tipos"]) if nome == "T" else inst[nome]
            f.write(f"set {nome} :=\n")
            for k in range(0, len(elems), 6):
                f.write("  " + " ".join(elems[k:k + 6]) + "\n")
            f.write(";\n\n")

        f.write(f"param DMAX_URB := {cfg['dmax_urb']};\n")
        f.write(f"param DMAX_NURB := {cfg['dmax_nurb']};\n")
        f.write(f"param CAP := {inst['cap']};\n")
        f.write(f"param META := {cfg.get('meta', 1.0)};\n")
        for k, v in (extras or {"ETAPA": 1}).items():
            f.write(f"param {k} := {v};\n")
        f.write("\nparam : t_eq t_custo :=\n")
        for t, d in inst["tipos"].items():
            f.write(f"  {t} {d['eq']} {d['custo']:.2f}\n")
        f.write(";\n\nparam : eq amp :=\n")
        for u in inst["U"]:
            f.write(f"  {u} {inst['eq'][u]} {inst['amp'][u]}\n")
        f.write(";\n\nparam mae :=\n")
        for a in inst["A"]:
            f.write(f"  {a} {inst['mae'][a]}\n")
        f.write(";\n\nparam : pop nurb :=\n")
        for i in inst["S"]:
            f.write(f"  {i} {inst['pop'][i]:.3f} {inst['nurb'][i]}\n")
        f.write(";\n\n/* so os pares ate a maior distancia maxima; os demais valem 999 (default) */\n")
        f.write("param dist :=\n")
        for (i, j), d in inst["dist"].items():
            if d <= dlim:
                f.write(f"  [{i},{j}] {d:.4f}\n")
        f.write(";\n\nend;\n")


if __name__ == "__main__":
    inst = monta_instancia(CENARIO_BASE)
    os.makedirs(DAT, exist_ok=True)
    saida = os.path.join(DAT, "dados_base.dat")
    escreve_dat(inst, saida)
    print(f"OK: {saida}")
    print(f"Setores: {len(inst['S'])} | UBS: {len(inst['U'])} | Apoios: {len(inst['A'])} | "
          f"Candidatos a UBS nova: {len(inst['N'])} | Populacao: {sum(inst['pop'].values()):,.0f}")
    print("UBS responsavel por cada ponto de apoio:",
          {a: inst['pontos'][u]['nome'] for a, u in inst['mae'].items()})
