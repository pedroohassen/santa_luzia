# acessibilidade.py
# Indices de acessibilidade espacial da aula 8 (Guagliardo, 2004), calculados FORA da otimizacao
# para a situacao atual e para os cenarios C1, C2 e C3. Nao decidem onde abrir UBS.
#   1) razao oferta/populacao (eSF por 1.000 hab. e vagas/populacao) por distrito e no municipio;
#   2) distancia ate a unidade mais proxima;
#   3) distancia media ate a unidade de referencia atribuida pelo modelo;
#   4) modelo gravitacional de Hansen (1959) com ajuste de demanda de Joseph e Bantock (1982):
#        V_j = sum_k P_k / d_kj^b ;  A_i = sum_j S_j / (d_ij^b * V_j)
#      S_j = vagas da unidade (eSF x 3.500); P_k = populacao do setor; d = matriz de distancias pela rede de ruas.
#      Premissa: d_ij >= raio equivalente do setor de origem, sqrt(area/pi), para evitar d = 0.
#      Beta (decaimento com a distancia) sem estimativa empirica para Santa Luzia: testado 1; 1,5; 2.
# Tambem grava a matriz de distancias (entrada do modelo e dos indices) em Excel.
# Saidas: resultados/matriz_distancias.xlsx, resultados/acessibilidade.json e .xlsx, dados/acessibilidade_*.dat
import json
import math
import os

import pandas as pd

from gera_dados import DAT, PASTA, hav, le_planilha, le_rede
from rede import CENARIOS_REDE, NOME_CENARIO, RES, carrega_rede, rid

BETAS = (1.0, 1.5, 2.0)
FAIXAS = [(0, 0.5, "até 0,5 km"), (0.5, 1, "0,5 a 1 km"), (1, 2, "1 a 2 km"), (2, 3, "2 a 3 km"),
          (3, 5, "3 a 5 km"), (5, 999, "mais de 5 km")]


def raio_equivalente(inst, i):
    return math.sqrt(inst["area"][i] / math.pi)


def drede(inst, unidades, i, j):
    """Distancia pela rede de ruas do centro do setor i ate a unidade j (km)."""
    return le_rede()[(f"S{i}", rid(inst, j, unidades))]


def gravitacional(inst, unidades, beta):
    """Devolve (A_hansen[i], A_ajustado[i], S[j], V[j]) conforme slides 27-29 da aula 8."""
    S_ = {j: u["eq"] * inst["cap"] for j, u in unidades.items()}
    P = inst["pop"]
    d = {(i, j): max(drede(inst, unidades, i, j), raio_equivalente(inst, i))
         for i in inst["S"] for j in unidades}
    db = {k: v ** beta for k, v in d.items()}
    V = {j: sum(P[k] / db[k, j] for k in inst["S"]) for j in unidades}
    A_h = {i: sum(S_[j] / db[i, j] for j in unidades) for i in inst["S"]}
    A = {i: sum(S_[j] / (db[i, j] * V[j]) for j in unidades) for i in inst["S"]}
    return A_h, A, S_, V


def spearman(x, y):
    """Correlacao de postos de Spearman (sem empates relevantes)."""
    ks = list(x)
    rx = {k: r for r, k in enumerate(sorted(ks, key=lambda k: x[k]))}
    ry = {k: r for r, k in enumerate(sorted(ks, key=lambda k: y[k]))}
    n = len(ks)
    return 1 - 6 * sum((rx[k] - ry[k]) ** 2 for k in ks) / (n * (n * n - 1))


def ponderada(valores, pesos):
    tot = sum(pesos.values())
    return sum(valores[k] * pesos[k] for k in valores) / tot


def quantil_ponderado(valores, pesos, q):
    ordem = sorted(valores, key=lambda k: valores[k])
    alvo, acum = q * sum(pesos.values()), 0.0
    for k in ordem:
        acum += pesos[k]
        if acum >= alvo:
            return valores[k]
    return valores[ordem[-1]]


def analisa(rede):
    inst, unid, aloc = rede["inst"], rede["unidades"], rede["aloc"]
    P, tot = inst["pop"], sum(inst["pop"].values())

    # 1) razao oferta/populacao por distrito
    oferta = {}
    for nome_d in sorted(set(inst["distrito"].values())) + ["Município"]:
        pop_d = sum(P[i] for i in inst["S"] if nome_d == "Município" or inst["distrito"][i] == nome_d)
        eq_d = sum(u["eq"] for u in unid.values() if nome_d == "Município" or u["distrito"] == nome_d)
        oferta[nome_d] = {"populacao": round(pop_d), "esf": eq_d,
                          "esf_por_1000_hab": round(1000 * eq_d / pop_d, 3),
                          "vagas_por_habitante": round(eq_d * inst["cap"] / pop_d, 3)}

    # 2) distancia ate a unidade mais proxima (setor urbanizado: so UBS; demais: UBS e pontos de apoio)
    dmin = {}
    for i in inst["S"]:
        cand = [drede(inst, unid, i, j) for j in unid]
        if inst["nurb"][i]:
            cand += [inst["dist"][i, a] for a in inst["A"]]
        dmin[i] = min(cand)
    mais_proxima = {"media_km": round(ponderada(dmin, P), 3), "maxima_km": round(max(dmin.values()), 2),
                    "faixas_pct": {r: round(100 * sum(P[i] for i in dmin if a < dmin[i] <= b) / tot, 2)
                                   for a, b, r in FAIXAS}}

    # 3) distancia media ate a unidade de referencia atribuida pelo modelo (populacao coberta)
    num = sum(P[i] * f * inst["dist"][i, j] for i, fr in aloc.items() for j, f in fr.items())
    cob = sum(P[i] * f for i, fr in aloc.items() for f in fr.values())
    dref_max = max(inst["dist"][i, j] for i, fr in aloc.items() for j in fr)
    referencia = {"media_km": round(num / cob, 3), "maxima_km": round(dref_max, 2),
                  "populacao_com_referencia": round(cob)}

    # 4) gravitacional
    grav, por_beta = {}, {}
    for b in BETAS:
        A_h, A, S_, V = gravitacional(inst, unid, b)
        por_beta[b] = (A_h, A, S_, V)
        grav[str(b)] = {
            "media_ponderada": round(ponderada(A, P), 4),
            "vagas_por_habitante_municipio": round(sum(S_.values()) / tot, 4),
            "p10": round(quantil_ponderado(A, P, 0.10), 4),
            "mediana": round(quantil_ponderado(A, P, 0.50), 4),
            "p90": round(quantil_ponderado(A, P, 0.90), 4),
            "minimo": round(min(A.values()), 4), "maximo": round(max(A.values()), 4),
            "pop_A_menor_que_1_pct": round(100 * sum(P[i] for i in A if A[i] < 1) / tot, 2),
            "pop_A_menor_que_0,75_pct": round(100 * sum(P[i] for i in A if A[i] < 0.75) / tot, 2),
            "pop_A_menor_que_0,5_pct": round(100 * sum(P[i] for i in A if A[i] < 0.5) / tot, 2),
        }
    # a ordem dos setores muda com beta?
    estab = {}
    for b1, b2 in ((1.0, 1.5), (1.5, 2.0), (1.0, 2.0)):
        A1, A2 = por_beta[b1][1], por_beta[b2][1]
        piores1 = set(sorted(A1, key=A1.get)[:40])
        piores2 = set(sorted(A2, key=A2.get)[:40])
        estab[f"{b1}-{b2}"] = {"spearman": round(spearman(A1, A2), 3),
                               "piores_40_setores_em_comum": len(piores1 & piores2)}
    A15 = por_beta[1.5][1]
    piores = sorted(A15, key=A15.get)[:40]
    estab["piores_40_por_distrito_beta1.5"] = {d: sum(1 for i in piores if inst["distrito"][i] == d)
                                               for d in sorted(set(inst["distrito"].values()))}
    estab["piores_40_rurais_beta1.5"] = sum(inst["nurb"][i] for i in piores)
    estab["piores_40_dist_media_mais_proxima_km"] = round(sum(dmin[i] for i in piores) / len(piores), 2)
    return dict(oferta=oferta, mais_proxima=mais_proxima, referencia=referencia, gravitacional=grav,
                estabilidade_beta=estab), por_beta, dmin


def matriz_excel(redes):
    """Matriz de distancias (setor x unidade), com populacao e classificacao urbano/rural."""
    inst = redes["C1"]["inst"]
    _, setores, _ = le_planilha()
    sit = {str(int(r["cd_setor"])): r["situacao"] for _, r in setores.iterrows()}
    pt = inst["pontos"]
    unidades = {}
    for u in inst["U"]:
        unidades[u] = dict(nome=pt[u]["nome"], tipo="UBS existente", eSF=inst["eq"][u], distrito=pt[u]["distrito"],
                           lat=pt[u]["lat"], lon=pt[u]["lon"])
    for a in inst["A"]:
        unidades[a] = dict(nome=pt[a]["nome"], tipo="Ponto de apoio rural", eSF=0, distrito=pt[a]["distrito"],
                           lat=pt[a]["lat"], lon=pt[a]["lon"])
    for cen in ("C1", "C2", "C3"):
        for j, u in redes[cen]["unidades"].items():
            if u["nova"]:
                if j in unidades:
                    unidades[j]["tipo"] += f", {cen}"
                    continue
                unidades[j] = dict(nome=u["nome"], tipo=f"UBS nova ({u['tipo']}) em {cen}", eSF=u["eq"],
                                   distrito=u["distrito"], lat=u["lat"], lon=u["lon"])
    set_df = pd.DataFrame([{"cd_setor": i, "distrito": inst["distrito"][i], "situacao_IBGE": sit[i],
                            "classe_raio": "rural/demais (raio 5 km)" if inst["nurb"][i] else "área urbanizada (raio 3 km)",
                            "populacao_MS_2025": round(inst["pop"][i], 1), "area_km2": inst["area"][i],
                            "raio_equivalente_km": round(raio_equivalente(inst, i), 4),
                            "latitude": inst["coord_s"][i][0], "longitude": inst["coord_s"][i][1]} for i in inst["S"]])
    uni_df = pd.DataFrame([dict(id=j, **u) for j, u in unidades.items()])
    todas = dict(inst["pontos"])
    for cen in ("C1", "C2", "C3"):
        todas.update({j: dict(u, rede=f"S{u['setor']}") for j, u in redes[cen]["unidades"].items() if u["nova"]})
    mat = pd.DataFrame({j: [round(le_rede()[(f"S{i}", todas[j]["rede"])], 4) for i in inst["S"]]
                        for j in unidades}, index=inst["S"])
    mat.index.name = "cd_setor"
    reta = pd.DataFrame({j: [round(hav(*inst["coord_s"][i], u["lat"], u["lon"]), 4) for i in inst["S"]]
                         for j, u in unidades.items()}, index=inst["S"])
    reta.index.name = "cd_setor"
    cand = pd.DataFrame({f"NOVA_{k[-8:]}": [round(inst["dist"][i, f"NOVA_{k[-8:]}"], 4) for i in inst["S"]]
                         for k in inst["S"]}, index=inst["S"])
    cand.index.name = "cd_setor"
    leia = pd.DataFrame({"Item": [
        "Distância", "Origem i (= k)", "Destino j", "Candidatos", "População", "Classe do raio", "Raio equivalente"],
        "Descrição": [
        "Trajeto pela rede de ruas (OpenStreetMap, rede a pé; caminho mínimo), em km. Aba 'Linha reta': Haversine, para comparação.",
        "Centro (ponto representativo) de cada um dos 398 setores censitários do IBGE 2022.",
        "UBS existentes (CNES), pontos de apoio rurais e UBS novas escolhidas em C1, C2 e C3.",
        "Aba 'Candidatos': distância de cada setor ao centro de cada setor (locais candidatos a UBS nova no modelo).",
        "Censo 2022 ajustado à estimativa do Ministério da Saúde 2025 (230.382 hab.).",
        "Área urbanizada de cidade ou vila (IBGE): raio de 3 km; demais situações: raio de 5 km (C1 e C3).",
        "sqrt(área/π): distância mínima adotada no índice gravitacional, para evitar d = 0."]})
    caminho = os.path.join(RES, "matriz_distancias.xlsx")
    with pd.ExcelWriter(caminho) as xw:
        leia.to_excel(xw, sheet_name="Leia-me", index=False)
        set_df.to_excel(xw, sheet_name="Setores", index=False)
        uni_df.to_excel(xw, sheet_name="Unidades", index=False)
        mat.to_excel(xw, sheet_name="Distancias")
        cand.to_excel(xw, sheet_name="Candidatos")
        reta.to_excel(xw, sheet_name="Linha reta")
    print("OK", caminho, mat.shape, cand.shape)

    # Trecho para o relatorio: os 6 setores mais populosos x UBS novas do C1, 2 UBS existentes e 1 ponto de apoio
    novas_c1 = [j for j, u in redes["C1"]["unidades"].items() if u["nova"]]
    cols = novas_c1 + inst["U"][:2] + inst["A"][:1]
    sets = sorted(inst["S"], key=lambda i: -inst["pop"][i])[:6]
    rot = {j: (f"UBS nova C1 ({redes['C1']['unidades'][j]['tipo'].replace('P', 'porte ')})" if j in novas_c1
               else unidades[j]["nome"].replace("Ponto de Apoio ", "Apoio ")) for j in cols}
    with open(os.path.join(RES, "trecho_matriz.csv"), "w", encoding="utf-8-sig") as f:
        f.write(";".join(["Setor", "População"] + [rot[j] for j in cols]) + "\n")
        for i in sets:
            f.write(";".join([i, f"{inst['pop'][i]:,.0f}".replace(",", ".")] +
                             [f"{mat.loc[i, j]:.2f}".replace(".", ",") for j in cols]) + "\n")


def dat_gmpl(rede, beta, caminho):
    """Dados no formato do slide 29 da aula 8 (acessibilidade.mod), para conferencia no GLPK."""
    inst, unid = rede["inst"], rede["unidades"]
    ids = list(unid)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(f"/* Acessibilidade gravitacional - cenario {rede['cen']} (gerado por acessibilidade.py) */\ndata;\n")
        f.write("set I := " + " ".join(inst["S"]) + ";\n")
        f.write("set K := " + " ".join(inst["S"]) + ";\n")
        f.write("set J := " + " ".join(ids) + ";\n")
        f.write(f"param Beta := {beta};\n")
        f.write("param S := " + " ".join(f"{j} {unid[j]['eq'] * inst['cap']:.0f}" for j in ids) + ";\n")
        f.write("param P := " + " ".join(f"{k} {inst['pop'][k]:.3f}" for k in inst["S"]) + ";\n")
        for nome in ("Di", "Dk"):
            f.write(f"param {nome} : " + " ".join(ids) + " :=\n")
            for i in inst["S"]:
                f.write(f"  {i} " + " ".join(
                    f"{max(drede(inst, unid, i, j), raio_equivalente(inst, i)):.5f}"
                    for j in ids) + "\n")
            f.write(";\n")
        f.write("end;\n")


def confere_glpk(rede, beta, A_python):
    """Roda o acessibilidade.mod (formato da aula) no GLPK e compara A_i com o calculo em Python."""
    import swiglpk as glp
    os.makedirs(DAT, exist_ok=True)
    dat = os.path.join(DAT, f"acessibilidade_{rede['cen']}_beta{beta}.dat")
    dat_gmpl(rede, beta, dat)
    saida = os.path.join(RES, f"acessibilidade_glpk_{rede['cen']}_beta{beta}.txt")
    glp.glp_term_out(glp.GLP_OFF)
    tran = glp.glp_mpl_alloc_wksp()
    erro = (glp.glp_mpl_read_model(tran, os.path.join(PASTA, "acessibilidade.mod"), 1)
            or glp.glp_mpl_read_data(tran, dat) or glp.glp_mpl_generate(tran, saida))
    if erro:
        glp.glp_mpl_free_wksp(tran)
        return {"conferido": False, "motivo": "erro do GLPK ao gerar o modelo"}
    lp = glp.glp_create_prob()
    glp.glp_mpl_build_prob(tran, lp)
    glp.glp_mpl_postsolve(tran, lp, glp.GLP_SOL)
    glp.glp_mpl_free_wksp(tran)
    glp.glp_delete_prob(lp)
    A_glpk = {}
    with open(saida, encoding="utf-8") as f:
        dentro = False
        for linha in f:
            if linha.startswith("Orig"):
                dentro = True
                continue
            if dentro and linha.startswith("["):
                k, v = linha.strip().split("\t")
                A_glpk[k.strip("[]")] = float(v)
    difmax = max(abs(A_glpk[i] - round(A_python[i], 4)) for i in A_glpk)
    return {"conferido": True, "setores": len(A_glpk), "diferenca_maxima": round(difmax, 6), "relatorio": os.path.basename(saida)}


def main():
    redes = {c: carrega_rede(c) for c in CENARIOS_REDE}
    matriz_excel(redes)
    saida, linhas_setor, linhas_unid = {}, [], []
    for c, rede in redes.items():
        res, por_beta, dmin = analisa(rede)
        saida[c] = res
        inst = rede["inst"]
        for b, (A_h, A, S_, V) in por_beta.items():
            for i in inst["S"]:
                linhas_setor.append({"cenario": c, "beta": b, "cd_setor": i, "distrito": inst["distrito"][i],
                                     "urbanizado": 1 - inst["nurb"][i], "populacao": round(inst["pop"][i], 1),
                                     "dist_unidade_mais_proxima_km": round(dmin[i], 3),
                                     "A_hansen": round(A_h[i], 2), "A_ajustado": round(A[i], 4),
                                     "lat": inst["coord_s"][i][0], "lon": inst["coord_s"][i][1]})
            for j, u in rede["unidades"].items():
                linhas_unid.append({"cenario": c, "beta": b, "unidade": u["nome"], "id": j, "nova": u["nova"],
                                    "eSF": u["eq"], "oferta_S": round(S_[j]), "demanda_V": round(V[j], 1)})
        if c == "C1":
            saida[c]["conferencia_glpk_beta1.5"] = confere_glpk(rede, 1.5, por_beta[1.5][1])
        g = res["gravitacional"]["1.5"]
        print(f"{NOME_CENARIO[c]:22s} | mais próxima {res['mais_proxima']['media_km']} km | referência "
              f"{res['referencia']['media_km']} km | A(b=1,5) média {g['media_ponderada']} "
              f"| pop A<1: {g['pop_A_menor_que_1_pct']}% | estab {res['estabilidade_beta']}")
    saida["premissas"] = {
        "betas": list(BETAS),
        "distancia_minima": "raio equivalente do setor de origem, sqrt(area_km2/pi)",
        "S_j": "vagas = eSF x 3.500 (UBS existentes e novas; pontos de apoio sem equipe propria ficam fora)",
        "P_k": "populacao do setor (Censo 2022 ajustado a estimativa MS 2025)",
        "piores_setores": "40 setores (10%) de menor A_ajustado"}
    with open(os.path.join(RES, "acessibilidade.json"), "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    with pd.ExcelWriter(os.path.join(RES, "acessibilidade.xlsx")) as xw:
        pd.DataFrame(linhas_unid).to_excel(xw, sheet_name="Unidades (S e V)", index=False)
        pd.DataFrame(linhas_setor).to_excel(xw, sheet_name="Setores (A)", index=False)
    print("OK", os.path.join(RES, "acessibilidade.json"))


if __name__ == "__main__":
    main()
