# robustez.py
# Testes de robustez do cenario principal (C1). As unidades do plano ficam FIXAS (UBS novas e eSF
# acrescidas nas UBS existentes); um programa inteiro so redistribui os setores (cada setor inteiro para
# uma unica unidade) para maximizar a cobertura efetiva.
#   1) Estresse de demanda (Monte Carlo): a populacao de cada setor varia ao acaso (+-10, 20, 30%),
#      com o total mantido; mede quanto da populacao o plano ainda cobre.
#   2) Crescimento populacional: o plano de hoje em 2026-2029 (cobertura com o plano fixo) e o
#      minimo de novas eSF para manter 100% em cada ano (etapa 1 do modelo).
# Saida: resultados/robustez.json. Rode depois de "python resolver.py cenarios".
import csv
import json
import os
import random
import statistics

import highspy

from gera_dados import CENARIO_BASE, RESULTADOS as RES, monta_instancia

PRINCIPAL = "C1"
SORTEIOS = 50           # por nivel; cada sorteio e um programa inteiro (limite de TEMPO_SORTEIO s)
TEMPO_SORTEIO = 45
NIVEIS = (0.10, 0.20, 0.30)
SEMENTE = 2026


def ler_csv(caminho):
    with open(caminho, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def cobertura_max(inst, novas, pop=None, ampl=None, base=None):
    """Maior populacao com cobertura efetiva, com as equipes fixas (setor inteiro para uma unidade).
    novas = {local da UBS nova: eSF}; ampl = {UBS existente: eSF acrescidas}; pop = populacao por setor.
    base = {setor: ponto} (vinculos do plano): vira a solucao inicial do solver, depois de retirar, em cada
    unidade que estourar a vaga, os setores que nao cabem. Assim o resultado nunca fica abaixo do plano."""
    pop = pop or inst["pop"]
    ampl = ampl or {}
    cfg, dist = inst["cfg"], inst["dist"]
    cap = {u: inst["cap"] * (inst["eq"][u] + ampl.get(u, 0)) for u in inst["U"]}
    cap.update({k: inst["cap"] * e for k, e in novas.items()})
    pontos = inst["U"] + inst["A"] + list(novas)
    pares = [(i, j) for i in inst["S"] for j in pontos
             if dist[i, j] <= (cfg["dmax_nurb"] if inst["nurb"][i] else cfg["dmax_urb"])
             and (inst["nurb"][i] or j not in inst["A"])]
    por_setor, por_unidade = {}, {}
    for k, (i, j) in enumerate(pares):
        por_setor.setdefault(i, []).append(k)
        por_unidade.setdefault(inst["mae"].get(j, j), []).append(k)  # apoio usa a vaga da UBS responsavel

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(TEMPO_SORTEIO))
    h.setOptionValue("mip_rel_gap", 1e-4)
    n = len(pares)
    h.addVars(n, [0.0] * n, [1.0] * n)
    if base:                                          # solucao inicial viavel a partir do plano
        ini, usado = [0.0] * n, {}
        idx = {par: k for k, par in enumerate(pares)}
        for i in sorted(base, key=lambda s: -pop[s]):
            k = idx.get((i, base[i]))
            u = inst["mae"].get(base[i], base[i])
            if k is not None and usado.get(u, 0) + pop[i] <= cap.get(u, 0) + 1e-6:
                ini[k], usado[u] = 1.0, usado.get(u, 0) + pop[i]

    h.changeColsIntegrality(n, list(range(n)), [highspy.HighsVarType.kInteger] * n)
    h.changeColsCost(n, list(range(n)), [-pop[i] for i, _ in pares])   # maximizar = minimizar o negativo
    inf = highspy.kHighsInf
    for ks in por_setor.values():                     # cada setor atendido no maximo 100%
        h.addRow(-inf, 1.0, len(ks), ks, [1.0] * len(ks))
    for u, ks in por_unidade.items():                 # vaga: populacao alocada <= 3.500 x eSF
        h.addRow(-inf, cap[u], len(ks), ks, [pop[pares[k][0]] for k in ks])
    if base:                                          # depois das restricoes: mudar o modelo descarta a solucao
        sol = highspy.HighsSolution()
        sol.col_value, sol.value_valid = ini, True
        h.setSolution(sol)
    h.run()
    return -h.getInfo().objective_function_value   # melhor solucao encontrada (cobertura garantida)


def plano_principal():
    plano = ler_csv(os.path.join(RES, PRINCIPAL, "plano.csv"))
    amp = ler_csv(os.path.join(RES, PRINCIPAL, "ampliacao.csv"))
    return {p["id"]: float(p["equipes"]) for p in plano}, {a["id"]: int(a["esf_novas"]) for a in amp}


def vinculos(cenario):
    return {a["setor"]: a["ponto_atendimento"]
            for a in ler_csv(os.path.join(RES, cenario, "alocacao.csv")) if float(a["fracao"]) > 0.5}


def estresse(inst, novas, ampl):
    rnd = random.Random(SEMENTE)
    tot = sum(inst["pop"].values())
    saida = {}
    for nivel in NIVEIS:
        cob = []
        for _ in range(SORTEIOS):
            p = {i: v * rnd.uniform(1 - nivel, 1 + nivel) for i, v in inst["pop"].items()}
            f = tot / sum(p.values())                     # mantem o total da cidade
            p = {i: v * f for i, v in p.items()}
            cob.append(100 * cobertura_max(inst, novas, p, ampl, vinculos(PRINCIPAL)) / tot)
        cob.sort()
        saida[f"{round(100 * nivel)}%"] = {
            "media_pct": round(statistics.mean(cob), 2), "minimo_pct": round(cob[0], 2),
            "p5_pct": round(cob[int(0.05 * len(cob))], 2), "maximo_pct": round(cob[-1], 2),
            "descobertos_medio": round(tot * (1 - statistics.mean(cob) / 100)),
            "sorteios": cob}
        print(f"  Estresse +-{round(100 * nivel)}%: media {saida[f'{round(100 * nivel)}%']['media_pct']}% "
              f"| pior {cob[0]:.2f}%")
    return saida


def crescimento(novas, ampl):
    import resolver                                 # import local: resolver le argumentos da linha de comando
    from resolver import eq_min, resolve_etapa
    resolver.TEMPO[1] = 900
    linhas = []
    for anos in range(0, 5):
        inst = monta_instancia(dict(CENARIO_BASE, nome=f"ano_{2025 + anos}", anos_crescimento=anos))
        tot = sum(inst["pop"].values())
        r1 = resolve_etapa(inst, {"ETAPA": 1, "EQ_MIN": eq_min(inst)})
        linhas.append({"ano": 2025 + anos, "populacao": round(tot),
                       "cobertura_plano_fixo_pct": round(100 * cobertura_max(inst, novas, None, ampl,
                                                                             vinculos(PRINCIPAL)) / tot, 2),
                       "novas_esf_minimas": round(r1["obj"], 2), "status": r1["status"],
                       "gap": r1["gap"]})
        print(f"  {2025 + anos}: pop {tot:,.0f} | plano fixo cobre {linhas[-1]['cobertura_plano_fixo_pct']}% "
              f"| minimo de novas eSF {r1['obj']:.0f} ({r1['status']})")
    return linhas


def main():
    inst = monta_instancia(dict(CENARIO_BASE))
    novas, ampl = plano_principal()
    tot = sum(inst["pop"].values())
    hoje = 100 * cobertura_max(inst, {}, base=vinculos("atual")) / tot
    com_plano = 100 * cobertura_max(inst, novas, None, ampl, vinculos(PRINCIPAL)) / tot
    print(f"Checagem: hoje {hoje:.2f}% | com o plano {PRINCIPAL} {com_plano:.2f}% (esperado 100%)")
    saida = {"cenario": PRINCIPAL,
             "checagem": {"hoje_pct": round(hoje, 2), "com_plano_pct": round(com_plano, 2)},
             "estresse_demanda": estresse(inst, novas, ampl),
             "crescimento": crescimento(novas, ampl),
             "premissas": {"sorteios_por_nivel": SORTEIOS, "semente": SEMENTE, "tempo_por_sorteio_s": TEMPO_SORTEIO,
                           "estresse": "populacao de cada setor x U(1-nivel, 1+nivel), total da cidade mantido",
                           "crescimento": "taxa anual Censo 2022 -> estimativa MS 2025, aplicada de 2026 a 2029"}}
    with open(os.path.join(RES, "robustez.json"), "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    print("OK", os.path.join(RES, "robustez.json"))


if __name__ == "__main__":
    main()
