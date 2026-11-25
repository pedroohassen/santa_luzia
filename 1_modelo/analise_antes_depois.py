# analise_antes_depois.py
# Indicadores descritivos da situacao atual (linha de base) e da situacao com o plano oficial,
# para o diagnostico e a comparacao "antes x depois" da apresentacao e do relatorio.
# Nao otimiza nada: le a planilha base e os resultados do cenario principal (resultados/C1).
import csv
import json
import os

import pandas as pd

from gera_dados import CENARIO_BASE, PLANILHA, RESULTADOS as RES, le_planilha, monta_instancia

FAIXAS = [(0, 0.5, "até 0,5 km"), (0.5, 1, "0,5 a 1 km"), (1, 2, "1 a 2 km"), (2, 3, "2 a 3 km"),
          (3, 5, "3 a 5 km"), (5, 99, "mais de 5 km")]
PRINCIPAL = "C1"
CAP = CENARIO_BASE["cap"]   # 3.500 pessoas por eSF (NT 2/2025-SAPS/MS)


def ler_csv(caminho):
    with open(caminho, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def limite(inst, i):
    return inst["cfg"]["dmax_nurb"] if inst["nurb"][i] else inst["cfg"]["dmax_urb"]


def acesso(inst, pontos_extra=()):
    """Distancia de cada setor ao ponto de APS mais proximo que ele pode usar
    (setor urbanizado: so UBS; demais setores: UBS e pontos de apoio), como no modelo."""
    pop, tot = inst["pop"], sum(inst["pop"].values())
    d = {}
    for i in inst["S"]:
        pts = inst["U"] + list(pontos_extra) + (inst["A"] if inst["nurb"][i] else [])
        d[i] = min(inst["dist"][i, j] for j in pts)
    fora = [i for i in inst["S"] if d[i] > limite(inst, i)]
    return {
        "distancia_media_km": round(sum(pop[i] * d[i] for i in d) / tot, 3),
        "distancia_maxima_km": round(max(d.values()), 2),
        "setores_fora_do_raio": len(fora),
        "pop_fora_do_raio": round(sum(pop[i] for i in fora)),
        "faixas_pct": {r: round(100 * sum(pop[i] for i in d if a < d[i] <= b) / tot, 2) for a, b, r in FAIXAS},
    }


def main():
    inst = monta_instancia(dict(CENARIO_BASE))
    _, setores, ind = le_planilha()
    eqs = pd.read_excel(PLANILHA, sheet_name="Equipes")
    esf = eqs[(eqs["tipo_equipe"] == "eSF") & (eqs["situacao"] == "Ativa")]

    resumo = json.load(open(os.path.join(RES, PRINCIPAL, "resumo.json"), encoding="utf-8"))
    I, A0 = resumo["indicadores"], resumo["situacao_atual"]
    plano = ler_csv(os.path.join(RES, PRINCIPAL, "plano.csv"))
    ocup = ler_csv(os.path.join(RES, PRINCIPAL, "ocupacao.csv"))
    aloc = ler_csv(os.path.join(RES, PRINCIPAL, "alocacao.csv"))
    pop_tot = I["populacao_total"]
    eq0, eq1 = I["equipes_atuais"], I["equipes_finais"]

    # Carga atual das equipes: usuarios cadastrados por eSF (e-Gestor/CNES)
    cad = [float(v) for v in esf["usuarios_cadastrados"]]
    por_ubs = (esf.groupby("estabelecimento")["usuarios_cadastrados"].agg(["sum", "count"]).reset_index())
    with open(os.path.join(RES, "carga_equipes_atual.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["unidade", "esf", "cadastrados", "cadastrados_por_esf"])
        for _, r in por_ubs.sort_values("sum", ascending=False).iterrows():
            w.writerow([r["estabelecimento"], int(r["count"]), int(r["sum"]),
                        round(r["sum"] / r["count"])])
    with open(os.path.join(RES, "cadastrados_por_esf_atual.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["unidade", "ine", "cadastrados"])
        for _, r in esf.sort_values("usuarios_cadastrados", ascending=False).iterrows():
            w.writerow([r["estabelecimento"], r["ine"], int(r["usuarios_cadastrados"])])

    # Acesso geografico: hoje e com as unidades novas do plano
    novas = [p["id"] for p in plano]
    antes, depois = acesso(inst), acesso(inst, novas)

    # Distancia ate a unidade de referencia definida pelo plano (alocacao)
    faixas_aloc = {r: 0.0 for _, _, r in FAIXAS}
    for a in aloc:
        if not a["distancia_km"]:
            continue
        dk, p = float(a["distancia_km"]), float(a["populacao_alocada"])
        for lo, hi, r in FAIXAS:
            if lo < dk <= hi or (lo == 0 and dk == 0):
                faixas_aloc[r] += p
    faixas_aloc = {r: round(100 * v / pop_tot, 2) for r, v in faixas_aloc.items()}

    situacao_setor = setores.groupby("situacao")["populacao"].sum()
    urb = float(situacao_setor.get("Área urbanizada de cidade ou vila", 0))
    pop_censo = float(setores["populacao"].sum())

    from gera_dados import le_porte
    pcnes = le_porte()
    pt = inst["pontos"]
    porte = {"total_folga_esf": sum(inst["porte"][u] - inst["eq"][u] for u in inst["U"]),
             "folga_por_distrito": {d: sum(inst["porte"][u] - inst["eq"][u] for u in inst["U"] if pt[u]["distrito"] == d)
                                    for d in sorted({pt[u]["distrito"] for u in inst["U"]})},
             "ubs_com_folga": sum(1 for u in inst["U"] if inst["porte"][u] > inst["eq"][u]),
             "ubs_sem_dado_de_porte": [pt[u]["nome"] for u in inst["U"] if int(u.split("_")[1]) not in pcnes],
             "porte_total_esf": sum(inst["porte"].values())}
    novas_amp = ler_csv(os.path.join(RES, PRINCIPAL, "ampliacao.csv"))
    saida = {
        "porte": porte,
        "linha_de_base": {
            "populacao": pop_tot,
            "area_km2": 233.76,
            "pop_area_urbanizada_pct": round(100 * urb / pop_censo, 1),
            "ubs": len(inst["U"]), "pontos_apoio": len(inst["A"]), "esf": eq0,
            "cadastro_esf_pct": round(100 * float(ind["usuarios_cadastrados_esf"]) / pop_tot, 1),
            "cobertura_potencial_egestor_pct": round(100 * float(ind["cobertura_potencial_aps"]), 1),
            "cobertura_potencial_3500_pct": A0["cobertura_potencial_atual_pct"],
            "cobertura_geografica_pct": A0["cobertura_geografica_atual_pct"],
            "cobertura_efetiva_pct": A0["cobertura_efetiva_atual_pct"],
            "descobertos": A0["descobertos_atual"],
            "descobertos_sem_vaga": A0["descobertos_sem_vaga"],
            "descobertos_longe": A0["descobertos_moram_longe"],
            "pop_por_esf": round(pop_tot / eq0),
            "cadastrados_por_esf_media": round(sum(cad) / len(cad)),
            "esf_acima_3500": sum(c > CAP for c in cad),
            "cadastrados_por_esf_max": int(max(cad)),
            "cobertura_acs_pct": round(100 * float(ind["cobertura_acs"]), 1),
            "cobertura_saude_bucal_pct": round(100 * float(ind["cobertura_saude_bucal"]), 1),
            "acesso_unidade_mais_proxima": antes,
        },
        "com_plano": {
            "cobertura_efetiva_pct": I["cobertura_efetiva_pct"],
            "descobertos": pop_tot - I["populacao_coberta"],
            "esf": eq1,
            "unidades_aps": len(inst["U"]) + len(novas),
            "pop_por_esf": round(pop_tot / eq1),
            "pessoas_por_esf_max": round(max(float(o["populacao_atendida"]) / float(o["equipes"]) for o in ocup)),
            "esf_acima_3500": sum(1 for o in ocup if float(o["populacao_atendida"]) / float(o["equipes"]) > CAP + 0.5),
            "ubs_novas": len(novas),
            "ubs_ampliadas": len(novas_amp),
            "esf_em_ubs_existentes": sum(int(r["esf_novas"]) for r in novas_amp),
            "cobertura_geografica_pct": I["cobertura_geografica_pct"],
            "ocupacao_media_pct": I["ocupacao_media_pct"],
            "distancia_media_unidade_referencia_km": I["distancia_media_km"],
            "distancia_maxima_unidade_referencia_km": I["distancia_maxima_km"],
            "faixas_unidade_referencia_pct": faixas_aloc,
            "acesso_unidade_mais_proxima": depois,
        },
    }
    with open(os.path.join(RES, "antes_depois.json"), "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print(json.dumps(saida, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
