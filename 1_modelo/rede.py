# rede.py
# Carrega a rede de APS de um cenario ja resolvido (resultados/<cenario>/): unidades com equipe
# (UBS existentes + UBS novas do cenario), alocacao dos setores e a instancia de dados.
# Usado pelas analises fora da otimizacao (acessibilidade, encaminhamentos, eSF volante).
import csv
import json
import os

from gera_dados import CENARIO_BASE, RESULTADOS as RES, monta_instancia

CENARIOS_REDE = ["atual", "C1", "C2", "C3"]
NOME_CENARIO = {"atual": "Situação atual", "C1": "C1 – Ideal", "C2": "C2 – Viável", "C3": "C3 – 99% + volante"}


def ler_csv(caminho):
    with open(caminho, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def carrega_rede(cen):
    pasta = os.path.join(RES, cen)
    resumo = json.load(open(os.path.join(pasta, "resumo.json"), encoding="utf-8"))
    cfg = dict(CENARIO_BASE, **{k: v for k, v in resumo["cenario"].items() if k in CENARIO_BASE})
    inst = monta_instancia(cfg)
    plano = ler_csv(os.path.join(pasta, "plano.csv"))
    pt = inst["pontos"]
    unidades = {u: dict(nome=pt[u]["nome"], lat=pt[u]["lat"], lon=pt[u]["lon"], eq=float(inst["eq"][u]),
                        distrito=pt[u]["distrito"], nova=False) for u in inst["U"]}
    amp_csv = os.path.join(pasta, "ampliacao.csv")
    ampl = {r["id"]: int(r["esf_novas"]) for r in ler_csv(amp_csv)} if os.path.exists(amp_csv) else {}
    for u, k in ampl.items():
        unidades[u]["eq"] += k
        unidades[u]["esf_novas"] = k
    for p in plano:
        unidades[p["id"]] = dict(nome=f"UBS nova (setor {p['setor']})", lat=float(p["latitude"]),
                                 lon=float(p["longitude"]), eq=float(p["equipes"]), distrito=p["distrito"],
                                 nova=True, tipo=p["tipo"], setor=p["setor"])
    aloc = {}
    for r in ler_csv(os.path.join(pasta, "alocacao.csv")):
        if r["ponto_atendimento"]:
            aloc.setdefault(r["setor"], {})[r["ponto_atendimento"]] = float(r["fracao"])
    return dict(cen=cen, inst=inst, unidades=unidades, aloc=aloc, resumo=resumo, plano=plano, ampl=ampl)


def rid(rede_ou_inst, j, unidades=None):
    """Id do ponto na matriz de distancias pela rede de ruas (dados/distancias_rede.csv)."""
    inst = rede_ou_inst["inst"] if "inst" in rede_ou_inst else rede_ou_inst
    pt = inst["pontos"]
    if j in pt:
        return pt[j]["rede"]
    return f"S{(unidades or rede_ou_inst['unidades'])[j]['setor']}"


def dono(inst, j):
    """Unidade com equipe responsavel por um ponto (ponto de apoio -> UBS responsavel)."""
    return inst["mae"].get(j, j)
