# resolver.py
# Resolve o modelo_aps.mod em etapas (lexicografico):
#   o GLPK le o modelo GMPL e gera o problema; o HiGHS resolve.
#
# Uso:
#   python resolver.py conferir             -> confere as solucoes gravadas, sem resolver (segundos)
#   python resolver.py cenarios             -> linha de base, C1, C2 e C3 (cenarios.csv)
#   python resolver.py atual                -> so a linha de base (rede de hoje); --tempo-atual=N
#   python resolver.py C1 --curva           -> cenario principal + curva com setores inteiros (lenta)
#   python resolver.py curva                -> curva do C1 com setores fracionaveis (aproximacao rapida)
#   python resolver.py sensibilidade        -> variantes do C1 (sensibilidade.csv)
#   python resolver.py integrado            -> modelo integrado em R$/ano (C1, C2, C3 e variacoes; integrado.csv)
#   python resolver.py repolir              -> melhora a designacao do C1-C3 a partir do integrado (mesmas unidades)
#   python resolver.py dat                  -> arquivos .dat do GLPK (C1, C2, C3 e integrado)
#   python resolver.py tabelas              -> refaz as tabelas a partir dos resultados
#   python resolver.py <cenario>            -> um cenario das listas CENARIOS, SENSIBILIDADE ou INTEGRADO
#   --glpk     resolve com o GLPK em vez do HiGHS
#   --tempo=N  limite de tempo por etapa (s); --inicio=PASTA solucao inicial de outro cenario
import json
import math
import os
import re
import sys
import tempfile
import time

import highspy
import swiglpk as glp

from gera_dados import CENARIO_BASE, DAT, PASTA, RESULTADOS, monta_instancia, escreve_dat, le_planilha

MODELO = os.path.join(PASTA, "modelo_aps.mod")
TEMPO = {1: 1800, 2: 1200, 3: 1800, 0: 300, 4: 1800}  # limite de tempo por etapa (s)
GAP = {1: 0.0, 2: 0.0, 3: 1e-4, 0: 1e-4, 4: 1e-4}    # gap relativo aceito (0 = exigir otimo provado)
PRINCIPAL = "C1"                          # cenario adotado como principal no relatorio

# Cenarios do trabalho (todos com 3.500 pessoas por eSF, distancia pela rede de ruas, setor indivisivel
# e eSF novas permitidas nas UBS existentes ate o porte)
CENARIOS = [
    {"nome": "C1"},                                       # Ideal: raios 3/5 km, 100%
    {"nome": "C2", "dmax_urb": 5.0, "dmax_nurb": 10.0, "inicio_de": "C1"},  # Viavel: raios 5/10 km, 100%
    {"nome": "C3", "meta": 0.99, "inicio_de": "C1"},      # raios 3/5 km, >= 99% + eSF volante
                                                          # (parte da solucao do C1, viavel no C3)
]

# Sensibilidade em torno do cenario principal (C1): uma premissa alterada por vez
SENSIBILIDADE = [
    {"nome": "C1"},
    {"nome": "cap_3000", "cap": 3000},                    # Portaria GM/MS 3.493/2024 (mun. > 100 mil hab.)
    {"nome": "censo_2022", "pop_ref": "censo"},
    {"nome": "pop_2029", "anos_crescimento": 4},
    {"nome": "dmax_urb_2", "dmax_urb": 2.0},
    {"nome": "dmax_nurb_3", "dmax_nurb": 3.0},
    {"nome": "reta", "distancia": "reta"},                # distancia em linha reta (modelo anterior)
    {"nome": "sem_ampliacao", "ampliacao": False, "inicio_de": None},  # sem eSF novas nas UBS existentes
    {"nome": "fracionado", "fracionado": True},           # setor pode ser dividido entre unidades
    {"nome": "com_eap", "eap": True},
    {"nome": "cand_1km", "candidatos": 1.0},              # candidatos so a mais de 1 km de uma UBS
]

# Modelo integrado (comparacao): minimiza o custo anual total em R$ (governo + populacao).
# Custo social de deslocamento por visita (Almeida et al., 2019): 2 r d + (2 d / s) m, com r = 0 (a pe).
# C_DESL = visitas/ano x 2 x m / s  (R$ por pessoa x km de distancia ate a unidade, por ano)
SALARIO_MINIMO_2025 = 1518.0           # Decreto n. 12.342/2024
PARAM_INT = {
    "visitas_ano": 2.5 * 0.63,         # Portaria GM/MS n. 1.101/2002: 2 a 3 consultas medicas/hab/ano, 63% basicas
    "renda_hora_R$": 2.2 * SALARIO_MINIMO_2025 / 220,  # IBGE CEMPRE 2021: 2,2 SM (Santa Luzia); 220 h/mes (CLT)
    "velocidade_kmh": 1.2 * 3.6,       # caminhada de 1,2 m/s (CONTRAN, MBST vol. V)
    "tarifa_km_R$": 0.0,               # a pe: sem tarifa
    "vida_util_anos": 25,              # edificacoes: 25 anos (4% ao ano; IN RFB n. 1.700/2017, anexo III)
}
PARAM_INT["c_desl"] = PARAM_INT["visitas_ano"] * (2 * PARAM_INT["tarifa_km_R$"]
                                                   + 2 * PARAM_INT["renda_hora_R$"] / PARAM_INT["velocidade_kmh"])

INTEGRADO = [
    {"nome": "INT_C1"},
    {"nome": "INT_C2", "dmax_urb": 5.0, "dmax_nurb": 10.0},
    {"nome": "INT_C3", "meta": 0.99},
    {"nome": "INT_desl_x0.5", "mult_desl": 0.5},
    {"nome": "INT_desl_x2", "mult_desl": 2.0},
    {"nome": "INT_desl_x5", "mult_desl": 5.0},
    {"nome": "INT_desl_x10", "mult_desl": 10.0},
    {"nome": "INT_desl_x20", "mult_desl": 20.0},
    {"nome": "INT_vida_10", "vida": 10},
]

# Referencias financeiras (comparacao, fora da otimizacao) - RAG 2025 de Santa Luzia
# (DigiSUS Gestor, dados SIOPS; despesas empenhadas em 2025)
FIN = {
    "despesa_saude_anual": 208260901.09,   # RAG 2025, item 9.1 (total)
    "despesa_aps_anual": 56189012.36,      # RAG 2025, item 9.1 (subfuncao 301, corrente + capital)
    "receita_impostos_transf": 432747323.36,  # RAG 2025, item 9.3 (III)
    "aplicado_asps_proprio": 101014133.21,    # RAG 2025, item 9.3 (XVI): 23,34% da receita
    "minimo_asps_lc141": 64912098.50,         # RAG 2025, item 9.3 (XVII): 15% da receita
    "horizonte_ppa_anos": 4,          # obras distribuidas em um ciclo de PPA/PMS (4 anos)
    "amortizacao_anos": 10,           # alternativa: obras amortizadas em 10 anos (premissa)
}

SOLVER = "glpk" if "--glpk" in sys.argv else "highs"
INICIO = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--inicio=")), None)  # pasta de solucao inicial
TEMPO_ATUAL = 300                         # limite de cada etapa da linha de base (situacao atual), em s
for _arg in sys.argv:
    if _arg.startswith("--tempo="):      # ex.: --tempo=2400 (segundos por etapa)
        TEMPO = {k: float(_arg.split("=")[1]) for k in TEMPO}
    if _arg.startswith("--tempo-atual="):  # ex.: --tempo-atual=1800 (linha de base)
        TEMPO_ATUAL = float(_arg.split("=")[1])



def resolve_etapa(inst, extras, inicio=None, tempo=None):
    """Traduz o GMPL com o GLPK, resolve (HiGHS ou GLPK) e devolve {nome_coluna: valor}."""
    with tempfile.TemporaryDirectory() as tmp:
        dat, mps = os.path.join(tmp, "d.dat"), os.path.join(tmp, "p.mps")
        escreve_dat(inst, dat, extras)
        glp.glp_term_out(glp.GLP_OFF)
        tran = glp.glp_mpl_alloc_wksp()
        if (glp.glp_mpl_read_model(tran, MODELO, 1) or glp.glp_mpl_read_data(tran, dat)
                or glp.glp_mpl_generate(tran, None)):
            glp.glp_term_out(glp.GLP_ON)
            sys.exit("Erro do GLPK ao gerar o modelo (rode glpsol para ver a mensagem).")
        lp = glp.glp_create_prob()
        glp.glp_mpl_build_prob(tran, lp)
        glp.glp_mpl_free_wksp(tran)
        if SOLVER == "glpk":
            return resolve_glpk(lp, extras["ETAPA"])
        glp.glp_write_mps(lp, glp.GLP_MPS_FILE, None, mps)
        glp.glp_delete_prob(lp)

        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", float(tempo or TEMPO[extras["ETAPA"]]))
        h.setOptionValue("mip_rel_gap", GAP[extras["ETAPA"]])
        h.readModel(mps)
    nomes = h.getLp().col_names_
    if inst["cfg"].get("fracionado"):   # comparacao: setor pode ser dividido (x continuo)
        idx = [k for k, n in enumerate(nomes) if n.startswith("x[")]
        h.changeColsIntegrality(len(idx), idx, [highspy.HighsVarType.kContinuous] * len(idx))
    if inicio:
        sol = highspy.HighsSolution()
        sol.col_value = [inicio.get(n, 0.0) for n in nomes]
        sol.value_valid = True
        h.setSolution(sol)
    t0 = time.time()
    h.run()
    info = h.getInfo()
    status = h.modelStatusToString(h.getModelStatus())
    if info.primal_solution_status != 2:  # 2 = solucao viavel encontrada
        return dict(status=status, viavel=False)
    valores = dict(zip(nomes, h.getSolution().col_value))
    return dict(status=status, viavel=True, obj=info.objective_function_value,
                limitante=info.mip_dual_bound, gap=info.mip_gap,
                tempo=round(time.time() - t0, 1), valores=valores)


def resolve_glpk(lp, etapa):
    """Alternativa 100% GLPK (mesmo algoritmo do glpsol)."""
    parm = glp.glp_iocp()
    glp.glp_init_iocp(parm)
    parm.presolve = glp.GLP_ON
    parm.tm_lim = int(TEMPO[etapa] * 1000)
    parm.mip_gap = GAP[etapa]
    t0 = time.time()
    glp.glp_intopt(lp, parm)
    st = glp.glp_mip_status(lp)
    status = {glp.GLP_OPT: "Optimal", glp.GLP_FEAS: "Feasible (limite de tempo)"}.get(st, "Sem solucao")
    if st not in (glp.GLP_OPT, glp.GLP_FEAS):
        return dict(status=status, viavel=False)
    valores = {glp.glp_get_col_name(lp, c): glp.glp_mip_col_val(lp, c)
               for c in range(1, glp.glp_get_num_cols(lp) + 1)}
    obj = glp.glp_mip_obj_val(lp)
    glp.glp_delete_prob(lp)
    return dict(status=status, viavel=True, obj=obj, limitante=obj if st == glp.GLP_OPT else None,
                gap=0.0 if st == glp.GLP_OPT else None, tempo=round(time.time() - t0, 1),
                valores=valores)


def le_solucao(valores):
    """Extrai decisoes das colunas (x, a, w): alocacao, eSF novas nas UBS existentes e UBS novas."""
    aloc, novas, ampl = {}, {}, {}   # aloc[setor] = {ponto: fracao}; novas[local] = tipo; ampl[ubs] = eSF
    for nome, v in valores.items():
        m = re.match(r"(\w+)\[(.*)\]$", nome)
        if not m:
            continue
        var, idx = m.group(1), m.group(2).split(",")
        if var == "x" and v > 1e-6:
            aloc.setdefault(idx[0], {})[idx[1]] = round(v) if abs(v - round(v)) < 1e-4 else v
        elif var == "w" and v > 0.5:
            novas[idx[0]] = idx[1]
        elif var == "a" and v > 0.5:
            ampl[idx[0]] = int(round(v))
    return aloc, novas, ampl


def limite(inst, i):
    return inst["cfg"]["dmax_nurb"] if inst["nurb"][i] else inst["cfg"]["dmax_urb"]


def pontos_do_setor(inst, i, novas=()):
    """Pontos que o setor pode usar: UBS existentes, UBS novas abertas e (so fora da area urbanizada) apoios."""
    return inst["U"] + list(novas) + (inst["A"] if inst["nurb"][i] else [])


def verifica(inst, aloc, novas, ampl):
    """Confere a solucao fora do solver, restricao por restricao."""
    dist, meta = inst["dist"], inst["cfg"].get("meta", 1.0)
    erros = []
    pop_tot = sum(inst["pop"].values())
    cob = 0.0
    for i in inst["S"]:
        frac = sum(aloc.get(i, {}).values())
        cob += inst["pop"][i] * frac
        if frac > 1 + 1e-6 or (meta >= 1 and abs(frac - 1) > 1e-6):
            erros.append(f"setor {i} com {100 * frac:.1f}% atendido")
        if not inst["cfg"].get("fracionado") and len(aloc.get(i, {})) > 1:
            erros.append(f"setor {i} dividido entre {len(aloc[i])} unidades")
        for j in aloc.get(i, {}):
            if dist[i, j] > limite(inst, i) + 1e-9:
                erros.append(f"setor {i} a {dist[i, j]:.2f} km de {j} (> {limite(inst, i)})")
            if j in inst["A"] and not inst["nurb"][i]:
                erros.append(f"setor urbano {i} atendido no ponto de apoio {j}")
            if j in inst["N"] and j not in novas:
                erros.append(f"setor {i} atendido em UBS nova nao aberta {j}")
    if cob < meta * pop_tot - 0.5:
        erros.append(f"cobertura {cob:.0f} abaixo da meta {meta * pop_tot:.0f}")
    carga = carga_por_unidade(inst, aloc)
    equipes = equipes_finais(inst, novas, ampl)
    for u, k in ampl.items():
        if k > inst["amp"][u]:
            erros.append(f"{u} recebeu {k} eSF, acima do porte (cabem {inst['amp'][u]})")
    for j, c in carga.items():
        excesso = c - inst["cap"] * equipes.get(j, 0)
        if excesso > 0.5:  # tolerancia numerica dos solvers: meia pessoa
            erros.append(f"{j} acima da capacidade em {excesso:.4f} pessoas")
    return erros


def carga_por_unidade(inst, aloc):
    """Populacao atendida por unidade com equipe (apoio conta na UBS responsavel)."""
    carga = {}
    for i, fr in aloc.items():
        for j, f in fr.items():
            dono = inst["mae"].get(j, j)
            carga[dono] = carga.get(dono, 0.0) + inst["pop"][i] * f
    return carga


def equipes_finais(inst, novas, ampl=None):
    eqs = {u: float(inst["eq"][u] + (ampl or {}).get(u, 0)) for u in inst["U"]}
    eqs.update({k: inst["tipos"][t]["eq"] for k, t in novas.items()})
    return eqs


def investimento(inst, novas):
    return sum(inst["tipos"][t]["custo"] for t in novas.values())


def cobertura_geografica(inst, novas=()):
    """Populacao com alguma unidade dentro do raio (sem considerar vaga)."""
    return sum(inst["pop"][i] for i in inst["S"]
               if min(inst["dist"][i, j] for j in pontos_do_setor(inst, i, novas)) <= limite(inst, i))


def situacao_atual(inst):
    """Cobertura efetiva sem nenhuma UBS nova e decomposicao dos descobertos."""
    pop, tot = inst["pop"], sum(inst["pop"].values())
    geo = cobertura_geografica(inst)
    r0 = resolve_etapa(inst, {"ETAPA": 0, "OBRIG": 0, "LIM_EQ": 0}, tempo=TEMPO_ATUAL)
    a0, _, _ = le_solucao(r0["valores"])
    cob = sum(pop[i] * f for i, fr in a0.items() for f in fr.values())
    # mesma cobertura maxima, agora com a menor distancia total (alocacao de referencia de hoje)
    inst_meta = dict(inst, cfg=dict(inst["cfg"], meta=max(0.0, (cob - 1.0) / tot)))
    rd = resolve_etapa(inst_meta, {"ETAPA": 3, "LIM_EQ": 0}, r0["valores"], tempo=TEMPO_ATUAL)
    if rd["viavel"]:
        a0, _, _ = le_solucao(rd["valores"])
    return {
        "cobertura_efetiva_atual_pct": round(100 * cob / tot, 2),
        "cobertura_geografica_atual_pct": round(100 * geo / tot, 2),
        "descobertos_atual": round(tot - cob),
        "descobertos_moram_longe": round(tot - geo),
        "descobertos_sem_vaga": round(geo - cob),
        "cobertura_potencial_atual_pct": round(100 * inst["cap"] * sum(inst["eq"].values()) / tot, 2),
        "status": r0["status"],
    }, a0


def financeiro(inst, novas_eq, invest):
    """Comparacao com o orcamento de saude (RAG 2025). Nao entra na otimizacao."""
    _, _, ind = le_planilha()
    eq_atuais = sum(inst["eq"].values())
    custo_eq_ano = FIN["despesa_aps_anual"] / eq_atuais          # custo medio da APS por eSF
    repasse_eq_ano = 12 * float(ind["repasse_esf_componente_fixo"] + ind["repasse_esf_componente_vinculo"]
                                + ind["repasse_esf_componente_qualidade"])
    bruto = novas_eq * custo_eq_ano
    repasse = novas_eq * repasse_eq_ano
    inv_ppa = invest / FIN["horizonte_ppa_anos"]
    inv_amort = invest / FIN["amortizacao_anos"]
    liquido = bruto - repasse
    return {
        "custo_medio_aps_por_esf_ano_R$": round(custo_eq_ano),
        "repasse_federal_por_esf_ano_R$": round(repasse_eq_ano),
        "custeio_novas_equipes_bruto_ano_R$": round(bruto),
        "repasse_federal_novas_equipes_ano_R$": round(repasse),
        "custeio_liquido_municipio_ano_R$": round(liquido),
        "obras_por_ano_no_PPA_R$": round(inv_ppa),
        "obras_amortizadas_ano_R$": round(inv_amort),
        "esforco_anual_PPA_R$": round(liquido + inv_ppa),
        "esforco_anual_amortizado_R$": round(liquido + inv_amort),
        "esforco_PPA_pct_despesa_APS": round(100 * (liquido + inv_ppa) / FIN["despesa_aps_anual"], 1),
        "esforco_PPA_pct_despesa_saude": round(100 * (liquido + inv_ppa) / FIN["despesa_saude_anual"], 1),
        "custeio_bruto_pct_despesa_APS": round(100 * bruto / FIN["despesa_aps_anual"], 1),
        "obras_total_pct_despesa_saude_anual": round(100 * invest / FIN["despesa_saude_anual"], 1),
        # se todo o esforco anual sair de recursos proprios (hipotese conservadora)
        "aplicacao_propria_asps_atual_pct": round(100 * FIN["aplicado_asps_proprio"] / FIN["receita_impostos_transf"], 2),
        "aplicacao_propria_asps_com_plano_pct": round(
            100 * (FIN["aplicado_asps_proprio"] + liquido + inv_ppa) / FIN["receita_impostos_transf"], 2),
        "folga_acima_minimo_lc141_R$": round(FIN["aplicado_asps_proprio"] - FIN["minimo_asps_lc141"]),
    }


def custos_anuais(inst, novas_eq, invest, dist_tot, c_desl=None, vida=None):
    """Custo anual em R$ de 2025 (mesma conta da funcao objetivo do modelo integrado), separado entre
    governo (custeio das eSF novas + obras anualizadas) e populacao (deslocamento)."""
    c_esf = FIN["despesa_aps_anual"] / sum(inst["eq"].values())
    c_desl = PARAM_INT["c_desl"] if c_desl is None else c_desl
    vida = PARAM_INT["vida_util_anos"] if vida is None else vida
    gov_eq, gov_obra, pessoas = c_esf * novas_eq, invest / vida, c_desl * dist_tot
    return {"custo_esf_ano_R$": round(c_esf), "c_desl_R$_por_pessoa_km_ano": round(c_desl, 4), "vida_util_anos": vida,
            "governo_custeio_esf_ano_R$": round(gov_eq), "governo_obras_anualizadas_R$": round(gov_obra),
            "governo_total_ano_R$": round(gov_eq + gov_obra), "populacao_deslocamento_ano_R$": round(pessoas),
            "custo_total_ano_R$": round(gov_eq + gov_obra + pessoas)}


def indicadores(inst, aloc, novas, ampl=None):
    ampl = ampl or {}
    pop, dist = inst["pop"], inst["dist"]
    tot = sum(pop.values())
    cob = sum(pop[i] * f for i, fr in aloc.items() for f in fr.values())
    dist_tot = sum(pop[i] * f * dist[i, j] for i, fr in aloc.items() for j, f in fr.items())
    carga = carga_por_unidade(inst, aloc)
    eqs = equipes_finais(inst, novas, ampl)
    cap_total = inst["cap"] * sum(eqs.values())
    novas_eq = sum(inst["tipos"][t]["eq"] for t in novas.values()) + sum(ampl.values())
    portes = {}
    for t in novas.values():
        portes[t] = portes.get(t, 0) + 1
    return {
        "populacao_total": round(tot),
        "populacao_coberta": round(cob),
        "cobertura_efetiva_pct": round(100 * cob / tot, 2),
        "cobertura_geografica_pct": round(100 * cobertura_geografica(inst, novas) / tot, 2),
        "descobertos": round(tot - cob),
        "equipes_atuais": sum(inst["eq"].values()),
        "novas_equipes": round(novas_eq, 2),
        "esf_em_ubs_existentes": sum(ampl.values()),
        "ubs_ampliadas": len(ampl),
        "esf_em_ubs_novas": round(sum(inst["tipos"][t]["eq"] for t in novas.values()), 2),
        "ubs_novas": len(novas),
        "portes_ubs_novas": " + ".join(f"{n}x{t}" for t, n in sorted(portes.items())),
        "equipes_finais": round(sum(eqs.values()), 2),
        "investimento_obras_R$": round(investimento(inst, novas), 2),
        "distancia_media_km": round(dist_tot / cob, 3) if cob else None,
        "pessoa_km": round(dist_tot, 1),
        "distancia_maxima_km": round(max(dist[i, j] for i, fr in aloc.items() for j in fr), 2) if aloc else None,
        "setores_divididos": sum(1 for fr in aloc.values() if len(fr) > 1),
        "ocupacao_media_pct": round(100 * cob / cap_total, 1),
        "ocupacao_maxima_pct": round(max(100 * carga[j] / (inst["cap"] * eqs[j]) for j in carga), 1),
        "ocupacao_minima_pct": round(min(100 * carga.get(j, 0) / (inst["cap"] * eqs[j]) for j in eqs), 1),
    }


def salva(inst, nome, resumo, aloc, novas, ampl=None):
    ampl = ampl or {}
    pasta = os.path.join(RESULTADOS, nome)
    os.makedirs(pasta, exist_ok=True)
    pt, pop, dist = inst["pontos"], inst["pop"], inst["dist"]
    with open(os.path.join(pasta, "resumo.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2)
    for chave, arquivo in (("curva_cobertura", "curva.csv"),):
        linhas = resumo["etapas"].get(chave)
        if linhas:
            with open(os.path.join(pasta, arquivo), "w", encoding="utf-8-sig") as f:
                f.write(";".join(linhas[0]) + "\n")
                for ln in linhas:
                    f.write(";".join(str(v) for v in ln.values()) + "\n")

    carga = carga_por_unidade(inst, aloc)
    with open(os.path.join(pasta, "ampliacao.csv"), "w", encoding="utf-8-sig") as f:
        f.write("id;unidade;distrito;esf_atuais;porte;esf_novas;esf_finais;populacao_atendida;latitude;longitude\n")
        for u in sorted(ampl, key=lambda u: pt[u]["nome"]):
            f.write(f"{u};{pt[u]['nome']};{pt[u]['distrito']};{inst['eq'][u]};{inst['porte'][u]};{ampl[u]};"
                    f"{inst['eq'][u] + ampl[u]};{carga.get(u, 0):.0f};{pt[u]['lat']};{pt[u]['lon']}\n")
    with open(os.path.join(pasta, "plano.csv"), "w", encoding="utf-8-sig") as f:
        f.write("id;unidade;setor;distrito;tipo;equipes;populacao_atendida;latitude;longitude;custo_obra_R$\n")
        for k, t in sorted(novas.items(), key=lambda kv: -pt[kv[0]]["lat"]):
            tp = inst["tipos"][t]
            f.write(f"{k};{tp['nome']};{pt[k]['setor']};{pt[k]['distrito']};{t};{tp['eq']};"
                    f"{carga.get(k, 0):.0f};{pt[k]['lat']};{pt[k]['lon']};{tp['custo']:.2f}\n")

    with open(os.path.join(pasta, "alocacao.csv"), "w", encoding="utf-8-sig") as f:
        f.write("setor;populacao_setor;fora_area_urbanizada;ponto_atendimento;nome_ponto;fracao;"
                "populacao_alocada;distancia_km;lat_setor;lon_setor\n")
        for i in inst["S"]:
            fr = dict(aloc.get(i) or {})
            resto = 1 - sum(fr.values())
            if resto > 1e-6:
                fr[""] = resto
            for j, fj in sorted(fr.items(), key=lambda t: -t[1]):
                d = f"{dist[i, j]:.3f}" if j else ""
                nome_j = pt[j]["nome"] if j else "SEM COBERTURA"
                f.write(f"{i};{pop[i]:.1f};{inst['nurb'][i]};{j};{nome_j};{fj:.4f};"
                        f"{pop[i] * fj:.1f};{d};{inst['coord_s'][i][0]};{inst['coord_s'][i][1]}\n")

    # Populacao sem cobertura efetiva (relevante no C3): onde esta e quao longe fica a UBS mais proxima
    sit = dict(zip(inst["_cd"], inst["_sit"]))
    with open(os.path.join(pasta, "descobertos.csv"), "w", encoding="utf-8-sig") as f:
        f.write("setor;distrito;fora_area_urbanizada;situacao_ibge;populacao_setor;populacao_descoberta;"
                "ubs_mais_proxima;distancia_ubs_mais_proxima_km;latitude;longitude\n")
        for i in inst["S"]:
            resto = pop[i] * (1 - sum((aloc.get(i) or {}).values()))
            if resto > 0.5:
                j = min(inst["U"] + list(novas), key=lambda u: dist[i, u])
                f.write(f"{i};{inst['distrito'][i]};{inst['nurb'][i]};{sit.get(i, '')};{pop[i]:.1f};{resto:.1f};"
                        f"{pt[j]['nome']};{dist[i, j]:.3f};{inst['coord_s'][i][0]};{inst['coord_s'][i][1]}\n")

    eqs = equipes_finais(inst, novas, ampl)
    with open(os.path.join(pasta, "ocupacao.csv"), "w", encoding="utf-8-sig") as f:
        f.write("id;unidade;distrito;equipes;capacidade;populacao_atendida;ocupacao_pct;latitude;longitude\n")
        for j in sorted(eqs, key=lambda j: pt[j]["nome"]):
            cap = inst["cap"] * eqs[j]
            f.write(f"{j};{pt[j]['nome']};{pt[j]['distrito']};{eqs[j]};{cap:.0f};{carga.get(j, 0):.0f};"
                    f"{100 * carga.get(j, 0) / cap:.1f};{pt[j]['lat']};{pt[j]['lon']}\n")
    return pasta


def gap_txt(r):
    return f"gap {100 * r['gap']:.3f}%" if r.get("gap") is not None else "gap desconhecido"


def etapa_resumo(r):
    return {k: r[k] for k in ("status", "obj", "limitante", "gap", "tempo")}


def aloca_min_distancia(inst, novas, ampl=None, tempo=600, inicio=None):
    """Com as unidades fixas (UBS novas e eSF acrescidas), aloca cada setor inteiro a uma unidade com a menor
    distancia total, respeitando a meta e as vagas (problema de designacao generalizada). Devolve (obj, aloc)."""
    ampl = ampl or {}
    cfg, dist, pop = inst["cfg"], inst["dist"], inst["pop"]
    meta = cfg.get("meta", 1.0)
    cap = {u: inst["cap"] * (inst["eq"][u] + ampl.get(u, 0)) for u in inst["U"]}
    cap.update({k: inst["cap"] * inst["tipos"][t]["eq"] for k, t in novas.items()})
    pontos = inst["U"] + inst["A"] + list(novas)
    pares = [(i, j) for i in inst["S"] for j in pontos
             if dist[i, j] <= limite(inst, i) and (inst["nurb"][i] or j not in inst["A"])]
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(tempo))
    h.setOptionValue("mip_rel_gap", 1e-4)
    n = len(pares)
    h.addVars(n, [0.0] * n, [1.0] * n)
    h.changeColsCost(n, list(range(n)), [pop[i] * dist[i, j] for i, j in pares])
    if not cfg.get("fracionado"):
        h.changeColsIntegrality(n, list(range(n)), [highspy.HighsVarType.kInteger] * n)
    por_setor, por_unidade = {}, {}
    for k, (i, j) in enumerate(pares):
        por_setor.setdefault(i, []).append(k)
        por_unidade.setdefault(inst["mae"].get(j, j), []).append(k)
    inf = highspy.kHighsInf
    for ks in por_setor.values():
        h.addRow(1.0 if meta >= 1 else -inf, 1.0, len(ks), ks, [1.0] * len(ks))
    if meta < 1:
        h.addRow(meta * sum(pop.values()), inf, n, list(range(n)), [pop[i] for i, _ in pares])
    for u, ks in por_unidade.items():
        h.addRow(-inf, cap[u], len(ks), ks, [pop[pares[k][0]] for k in ks])
    if inicio:
        sol = highspy.HighsSolution()
        sol.col_value = [float(inicio.get(i, {}).get(j, 0.0)) for i, j in pares]
        sol.value_valid = True
        h.setSolution(sol)
    h.run()
    if h.getInfo().primal_solution_status != 2:
        return None
    x = h.getSolution().col_value
    aloc = {}
    for k, (i, j) in enumerate(pares):
        if x[k] > 1e-6:
            aloc.setdefault(i, {})[j] = round(x[k]) if abs(x[k] - round(x[k])) < 1e-4 else x[k]
    return h.getInfo().objective_function_value, aloc


def passo_eq(inst):
    """Menor fracao de eSF possivel entre os tipos de UBS nova (1 sem eAP; 0,25 com eAP)."""
    return 0.25 if any(t["eq"] % 1 for t in inst["tipos"].values()) else 1.0


def eq_min(inst):
    """Corte valido da etapa 1: vagas que faltam para a meta / CAP, arredondado ao passo dos tipos."""
    pop = sum(inst["pop"].values())
    falta = (inst["cfg"].get("meta", 1.0) * pop - inst["cap"] * sum(inst["eq"].values())) / inst["cap"]
    p = passo_eq(inst)
    return max(0.0, p * math.ceil(falta / p - 1e-9))


def salva_atual(inst, atual, a0):
    """Linha de base: rede de hoje, cobertura maxima e, com ela, a menor distancia (resultados/atual)."""
    cfg = inst["cfg"]
    ind0 = indicadores(inst, a0, {})
    salva(inst, "atual", {"cenario": dict(cfg, nome="atual"), "situacao_atual": atual, "etapas": {},
                          "indicadores": ind0, "financeiro": financeiro(inst, 0, 0),
                          "custos_anuais": custos_anuais(inst, 0, 0, ind0["pessoa_km"]),
                          "referencias_financeiras": FIN, "parametros_integrado": PARAM_INT}, a0, {})


def inicio_de(inst, nome):
    """Solucao inicial a partir de um cenario ja resolvido (ex.: o C1, que cobre 100%, e viavel no C3)."""
    if not nome:
        return None
    pasta = os.path.join(RESULTADOS, nome)
    if not os.path.exists(os.path.join(pasta, "alocacao.csv")):
        return None
    v = {"NOVAS_EQ": 0.0, "INVEST": 0.0, "DIST_TOT": 0.0, "COBERTOS": 0.0}
    for p in ler(os.path.join(pasta, "plano.csv")):
        if p["id"] not in inst["N"]:
            return None
        v[f"w[{p['id']},{p['tipo']}]"] = 1.0
        v["NOVAS_EQ"] += float(p["equipes"])
        v["INVEST"] += float(p["custo_obra_R$"])
    if os.path.exists(os.path.join(pasta, "ampliacao.csv")):
        for p in ler(os.path.join(pasta, "ampliacao.csv")):
            k = min(int(p["esf_novas"]), inst["amp"].get(p["id"], 0))
            v[f"a[{p['id']}]"] = float(k)
            v["NOVAS_EQ"] += k
    for a in ler(os.path.join(pasta, "alocacao.csv")):
        if a["ponto_atendimento"]:
            i, j, f = a["setor"], a["ponto_atendimento"], float(a["fracao"])
            if (i, j) not in inst["dist"]:
                continue
            v[f"x[{i},{j}]"] = f
            v["COBERTOS"] += inst["pop"][i] * f
            v["DIST_TOT"] += inst["pop"][i] * f * inst["dist"][i, j]
    print(f"  Solucao inicial: cenario {nome} ({v['NOVAS_EQ']:.0f} novas eSF)")
    return v


def ler(caminho):
    import csv
    with open(caminho, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def instancia(ajustes):
    cfg = dict(CENARIO_BASE, **ajustes)
    if SOLVER == "glpk":
        cfg["nome"] += "_glpk"
    inst = monta_instancia(cfg)
    _, setores, _ = le_planilha()
    inst["_cd"] = [str(int(c)) for c in setores["cd_setor"]]
    inst["_sit"] = list(setores["situacao"])
    return inst


def roda_cenario(ajustes, curva=False):
    inst = instancia(ajustes)
    cfg = inst["cfg"]
    print(f"\n=== Cenario '{cfg['nome']}' | pop {sum(inst['pop'].values()):,.0f} | "
          f"{inst['cap']:.0f} pessoas/eSF | raios {cfg['dmax_urb']}/{cfg['dmax_nurb']} km ({cfg['distancia']}) | "
          f"meta {100 * cfg['meta']:.0f}% | {len(inst['N'])} candidatos | ampliacao ate {sum(inst['amp'].values())} eSF ===",
          flush=True)

    atual, a0 = situacao_atual(inst)
    if cfg["nome"] == PRINCIPAL:
        salva_atual(inst, atual, a0)
    print(f"  Hoje: cobertura efetiva {atual['cobertura_efetiva_atual_pct']}% | geografica "
          f"{atual['cobertura_geografica_atual_pct']}% | descobertos {atual['descobertos_atual']:,} "
          f"(longe {atual['descobertos_moram_longe']:,}; sem vaga {atual['descobertos_sem_vaga']:,})", flush=True)
    etapas = {}

    ini0 = inicio_de(inst, INICIO or cfg.get("inicio_de"))
    emin = eq_min(inst)
    r1 = resolve_etapa(inst, {"ETAPA": 1, "EQ_MIN": emin}, ini0)
    if not r1["viavel"]:
        print("  Etapa 1 sem solucao viavel:", r1["status"],
              "(problema inviavel)" if r1["status"] == "Infeasible" else "(limite de tempo; nao provado inviavel)")
        return None
    n1 = round(r1["obj"], 2)
    etapas["1_min_equipes"] = etapa_resumo(r1)
    print(f"  Etapa 1 - novas equipes: {n1} ({r1['status']}, {gap_txt(r1)}, {r1['tempo']} s)", flush=True)

    ini2 = r1["valores"]
    if ini0 and ini0["NOVAS_EQ"] <= n1 + 1e-6 and ini0["INVEST"] < r1["valores"].get("INVEST", 1e18):
        ini2 = ini0
    r2 = resolve_etapa(inst, {"ETAPA": 2, "LIM_EQ": n1 + 1e-6, "EQ_MIN": emin}, ini2)
    if not r2["viavel"]:
        r2 = dict(r1, obj=r1["valores"].get("INVEST", 0.0), status=r2["status"] + " (usada a etapa 1)")
    inv2 = r2["obj"]
    etapas["2_min_investimento"] = etapa_resumo(r2)
    print(f"  Etapa 2 - investimento: R$ {inv2:,.2f} ({r2['status']}, {gap_txt(r2)}, {r2['tempo']} s)", flush=True)

    ini3 = r2["valores"]   # ponto de partida da etapa 3: o melhor entre a etapa 2 e a solucao inicial, se viavel
    if (ini0 and ini0["NOVAS_EQ"] <= n1 + 1e-6 and ini0["INVEST"] <= inv2 + 1.0
            and ini0["DIST_TOT"] < r2["valores"].get("DIST_TOT", 1e18)):
        ini3 = ini0
    r3 = resolve_etapa(inst, {"ETAPA": 3, "LIM_EQ": n1 + 1e-6, "LIM_INV": round(inv2 + 1.0, 2),
                              "EQ_MIN": emin}, ini3)
    if not r3["viavel"]:
        r3 = dict(r2, obj=r2["valores"].get("DIST_TOT", 0.0), status=r3["status"] + " (usada a etapa 2)")
    etapas["3_min_distancia"] = etapa_resumo(r3)
    print(f"  Etapa 3 - pessoa x km: {r3['obj']:,.1f} ({r3['status']}, {gap_txt(r3)}, {r3['tempo']} s)", flush=True)

    aloc, novas, ampl = le_solucao(r3["valores"])
    # polimento: com as unidades fixas (UBS novas e eSF acrescidas), a designacao de menor distancia e um
    # problema menor; resolve-o a parte e usa a solucao se for melhor
    pol = aloca_min_distancia(inst, novas, ampl, tempo=600, inicio=aloc)
    if pol and pol[0] < r3["obj"] - 1e-3:
        print(f"  Polimento (designacao com unidades fixas): {r3['obj']:,.1f} -> {pol[0]:,.1f} pessoa x km")
        etapas["3_min_distancia"]["polimento"] = round(pol[0], 1)
        aloc = pol[1]
    erros = verifica(inst, aloc, novas, ampl)
    print("  Verificacao independente:", "OK" if not erros else erros[:5])
    ind = indicadores(inst, aloc, novas, ampl)
    ind["verificacao"] = "OK" if not erros else erros
    fin = financeiro(inst, ind["novas_equipes"], ind["investimento_obras_R$"])
    custos = custos_anuais(inst, ind["novas_equipes"], ind["investimento_obras_R$"], ind["pessoa_km"])

    if curva:
        pontos_curva, ini = [], None
        passos = list(range(0, math.ceil(n1))) + [n1]
        for k in passos:
            rc = resolve_etapa(inst, {"ETAPA": 0, "OBRIG": 0, "LIM_EQ": k + 1e-6}, ini)
            a_c, _, _ = le_solucao(rc["valores"])
            cob = sum(inst["pop"][i] * f for i, fr in a_c.items() for f in fr.values())
            pontos_curva.append({"novas_equipes": k, "populacao_coberta": round(cob),
                                 "cobertura_efetiva_pct": round(100 * cob / ind["populacao_total"], 2),
                                 "status": rc["status"], "gap": rc["gap"]})
            print(f"  Curva: {k:5} novas equipes -> {pontos_curva[-1]['cobertura_efetiva_pct']:6.2f}% "
                  f"({rc['status']})", flush=True)
            ini = rc["valores"]
        etapas["curva_cobertura"] = pontos_curva

    resumo = {"cenario": cfg, "situacao_atual": atual, "etapas": etapas, "indicadores": ind, "financeiro": fin,
              "custos_anuais": custos, "referencias_financeiras": FIN, "parametros_integrado": PARAM_INT}
    pasta = salva(inst, cfg["nome"], resumo, aloc, novas, ampl)
    print("  eSF em UBS existentes:", {inst["pontos"][u]["nome"]: k for u, k in ampl.items()})
    print("  UBS novas:", {inst["pontos"][k]["nome"]: t for k, t in novas.items()})
    print("  Indicadores:", json.dumps(ind, ensure_ascii=False))
    print("  Custos anuais:", json.dumps(custos, ensure_ascii=False))
    print("  Resultados em:", pasta, flush=True)
    return dict(atual, **ind, **fin)


def le_plano(nome):
    """Unidades de um resultado gravado: UBS novas {local: tipo}, eSF acrescidas {UBS: eSF} e designacao."""
    pasta = os.path.join(RESULTADOS, nome)
    novas = {p["id"]: p["tipo"] for p in ler(os.path.join(pasta, "plano.csv"))}
    ampl = {p["id"]: int(p["esf_novas"]) for p in ler(os.path.join(pasta, "ampliacao.csv"))}
    aloc = {}
    for a in ler(os.path.join(pasta, "alocacao.csv")):
        if a["ponto_atendimento"] and float(a["fracao"]) > 1e-6:
            f = float(a["fracao"])
            aloc.setdefault(a["setor"], {})[a["ponto_atendimento"]] = round(f) if abs(f - round(f)) < 1e-4 else f
    return novas, ampl, aloc


def repolir(aj, tempo=1200):
    """Se uma rodada do modelo integrado chegou as MESMAS unidades do cenario com uma designacao de menor
    distancia (a etapa 3 pode parar no limite de tempo), refaz o polimento partindo dela e grava a melhor.
    As etapas 1 e 2 (eSF e obras) nao mudam; so a designacao dos setores (etapa 3)."""
    nome = aj["nome"]
    rint = "INT_" + nome
    if not all(os.path.exists(os.path.join(RESULTADOS, c, "resumo.json")) for c in (nome, rint)):
        return
    inst = instancia(aj)
    novas, ampl, aloc = le_plano(nome)
    n2, a2, aloc2 = le_plano(rint)
    if (novas, ampl) != (n2, a2):
        print(f"  {nome}: unidades diferentes das do {rint}; nada a repolir")
        return
    dt = lambda al: sum(inst["pop"][i] * f * inst["dist"][i, j] for i, fr in al.items() for j, f in fr.items())
    d0, d2 = dt(aloc), dt(aloc2)
    inicio = aloc2 if d2 < d0 else aloc
    pol = aloca_min_distancia(inst, novas, ampl, tempo=tempo, inicio=inicio)
    cand = [(d0, aloc), (d2, aloc2)] + ([pol] if pol else [])
    melhor, nova = min(cand, key=lambda c: c[0])
    if melhor >= d0 - 1e-3:
        print(f"  {nome}: designacao ja e a melhor conhecida ({d0:,.1f} pessoa x km)")
        return
    erros = verifica(inst, nova, novas, ampl)
    if erros:
        print(f"  {nome}: repolimento descartado, verificacao falhou:", erros[:5])
        return
    caminho = os.path.join(RESULTADOS, nome, "resumo.json")
    res = json.load(open(caminho, encoding="utf-8"))
    ind = indicadores(inst, nova, novas, ampl)
    ind["verificacao"] = "OK"
    res["indicadores"] = ind
    res["financeiro"] = financeiro(inst, ind["novas_equipes"], ind["investimento_obras_R$"])
    res["custos_anuais"] = custos_anuais(inst, ind["novas_equipes"], ind["investimento_obras_R$"], ind["pessoa_km"])
    res["etapas"]["3_min_distancia"]["repolimento"] = {
        "antes": round(d0, 1), "depois": round(melhor, 1),
        "nota": f"designacao refeita com as mesmas unidades, partindo da solucao do {rint}"}
    salva(inst, nome, res, nova, novas, ampl)
    for a in INTEGRADO:                          # o modelo base avaliado nos precos de cada rodada integrada
        p = os.path.join(RESULTADOS, a["nome"], "resumo.json")
        if not os.path.exists(p):
            continue
        ri = json.load(open(p, encoding="utf-8"))
        if ri.get("referencia_lexicografica", {}).get("cenario") != nome:
            continue
        pi = ri["parametros_integrado"]
        ri["referencia_lexicografica"]["custos_anuais"] = custos_anuais(
            inst, ind["novas_equipes"], ind["investimento_obras_R$"], ind["pessoa_km"], pi["c_desl"], pi["vida_util_anos"])
        json.dump(ri, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  {nome}: repolimento {d0:,.1f} -> {melhor:,.1f} pessoa x km; verificacao OK", flush=True)


def conferir():
    """Confere, sem resolver nada, todas as solucoes gravadas: restricao por restricao, fora do solver."""
    ok = True
    for aj in CENARIOS + [a for a in SENSIBILIDADE if a["nome"] != PRINCIPAL] + INTEGRADO:
        nome = aj["nome"]
        if not os.path.exists(os.path.join(RESULTADOS, nome, "resumo.json")):
            continue
        inst = instancia({k: v for k, v in aj.items() if k not in ("mult_desl", "vida", "inicio_de")})
        novas, ampl, aloc = le_plano(nome)
        erros = verifica(inst, aloc, novas, ampl)
        ok = ok and not erros
        ind = indicadores(inst, aloc, novas, ampl)
        print(f"  {nome:14s} eSF novas {ind['novas_equipes']:>5} | obras R$ {ind['investimento_obras_R$']:>13,.0f} | "
              f"cobertura {ind['cobertura_efetiva_pct']:6.2f}% | distancia media {ind['distancia_media_km']:.3f} km | "
              f"{'OK' if not erros else erros[:3]}", flush=True)
    print("TODAS AS SOLUCOES CONFERIDAS: OK" if ok else "HA SOLUCOES COM ERRO")


def gera_dats():
    """Arquivos .dat do GLPK: etapas 1 a 3 do modelo base e modelo integrado, para C1, C2 e C3. Os limites das
    etapas 2 e 3 (LIM_EQ e LIM_INV) sao os valores obtidos nas etapas anteriores, gravados em resumo.json."""
    os.makedirs(DAT, exist_ok=True)
    for aj in CENARIOS:
        nome, inst = aj["nome"], instancia({k: v for k, v in aj.items() if k != "inicio_de"})
        e = json.load(open(os.path.join(RESULTADOS, nome, "resumo.json"), encoding="utf-8"))["etapas"]
        lim_eq = round(e["1_min_equipes"]["obj"] + 1e-6, 6)
        lim_inv = round(e["2_min_investimento"]["obj"] + 1.0, 2)
        c_esf = FIN["despesa_aps_anual"] / sum(inst["eq"].values())
        emin = eq_min(inst)
        for arq, extras in {
            "etapa1_min_equipes": {"ETAPA": 1, "EQ_MIN": emin},
            "etapa2_min_obras": {"ETAPA": 2, "LIM_EQ": lim_eq, "EQ_MIN": emin},
            "etapa3_min_distancia": {"ETAPA": 3, "LIM_EQ": lim_eq, "LIM_INV": lim_inv, "EQ_MIN": emin},
            "integrado_custo_anual": {"ETAPA": 4, "C_ESF": round(c_esf, 2), "VIDA": PARAM_INT["vida_util_anos"],
                                      "C_DESL": round(PARAM_INT["c_desl"], 6), "EQ_MIN": emin},
        }.items():
            escreve_dat(inst, os.path.join(DAT, f"{nome}_{arq}.dat"), extras)
            print("  ", f"{nome}_{arq}.dat")


def melhor_inicio(aj, ref, c_esf, c_desl, vida):
    """Solucao inicial do integrado: entre o cenario de referencia e as rodadas integradas ja feitas com as
    mesmas restricoes, a de menor custo anual nos precos desta rodada (evita que o limite de tempo deixe uma
    rodada num plano pior que um ja conhecido)."""
    mesmas = [a["nome"] for a in INTEGRADO
              if {k: v for k, v in a.items() if k not in ("nome", "mult_desl", "vida")} ==
              {k: v for k, v in aj.items() if k not in ("nome", "mult_desl", "vida")}]
    custo = {}
    for nome in [ref] + mesmas:
        p = os.path.join(RESULTADOS, nome, "resumo.json")
        if os.path.exists(p):
            i = json.load(open(p, encoding="utf-8"))["indicadores"]
            custo[nome] = c_esf * i["novas_equipes"] + i["investimento_obras_R$"] / vida + c_desl * i["pessoa_km"]
    return min(custo, key=custo.get) if custo else ref


def roda_integrado(aj):
    """Modelo de comparacao: uma unica funcao objetivo em R$/ano (governo + populacao), mesmas restricoes."""
    base = {k: v for k, v in aj.items() if k not in ("mult_desl", "vida")}
    inst = instancia(base)
    cfg = inst["cfg"]
    c_esf = FIN["despesa_aps_anual"] / sum(inst["eq"].values())
    c_desl = PARAM_INT["c_desl"] * aj.get("mult_desl", 1.0)
    vida = aj.get("vida", PARAM_INT["vida_util_anos"])
    ref = {"INT_C2": "C2", "INT_C3": "C3"}.get(aj["nome"], "C1")
    print(f"\n=== Integrado '{aj['nome']}' | C_DESL R$ {c_desl:.2f}/pessoa.km.ano | vida {vida} anos | "
          f"eSF R$ {c_esf:,.0f}/ano | referencia {ref} ===", flush=True)
    ini = inicio_de(inst, INICIO or melhor_inicio(aj, ref, c_esf, c_desl, vida))
    r = resolve_etapa(inst, {"ETAPA": 4, "C_ESF": round(c_esf, 2), "VIDA": vida, "C_DESL": round(c_desl, 6),
                             "EQ_MIN": eq_min(inst)}, ini)
    if not r["viavel"]:
        print("  sem solucao:", r["status"])
        return None
    aloc, novas, ampl = le_solucao(r["valores"])
    erros = verifica(inst, aloc, novas, ampl)
    print(f"  Custo total: R$ {r['obj']:,.0f}/ano ({r['status']}, {gap_txt(r)}, {r['tempo']} s); verificacao:",
          "OK" if not erros else erros[:5], flush=True)
    ind = indicadores(inst, aloc, novas, ampl)
    ind["verificacao"] = "OK" if not erros else erros
    custos = custos_anuais(inst, ind["novas_equipes"], ind["investimento_obras_R$"], ind["pessoa_km"], c_desl, vida)
    # a solucao do modelo base (lexicografico) avaliada com os mesmos precos, para comparacao direta
    rr = json.load(open(os.path.join(RESULTADOS, ref, "resumo.json"), encoding="utf-8"))["indicadores"]
    custos_ref = custos_anuais(inst, rr["novas_equipes"], rr["investimento_obras_R$"], rr["pessoa_km"], c_desl, vida)
    resumo = {"cenario": dict(cfg, mult_desl=aj.get("mult_desl", 1.0), vida=vida), "situacao_atual": {},
              "etapas": {"4_custo_total": etapa_resumo(r)}, "indicadores": ind,
              "financeiro": financeiro(inst, ind["novas_equipes"], ind["investimento_obras_R$"]),
              "custos_anuais": custos, "referencia_lexicografica": {"cenario": ref, "custos_anuais": custos_ref},
              "referencias_financeiras": FIN, "parametros_integrado": dict(PARAM_INT, c_desl=c_desl, vida_util_anos=vida)}
    salva(inst, aj["nome"], resumo, aloc, novas, ampl)
    print("  eSF em UBS existentes:", {inst["pontos"][u]["nome"]: k for u, k in ampl.items()})
    print("  UBS novas:", {inst["pontos"][k]["nome"]: t for k, t in novas.items()})
    print("  Custos (integrado):", json.dumps(custos, ensure_ascii=False))
    print("  Custos (base", ref + "):", json.dumps(custos_ref, ensure_ascii=False), flush=True)
    return custos


def monta_tabela(lista, arquivo):
    """Monta a tabela a partir dos resumo.json ja gravados (permite rodar os cenarios em paralelo)."""
    tabela = []
    for aj in lista:
        caminho = os.path.join(RESULTADOS, aj["nome"], "resumo.json")
        if not os.path.exists(caminho):
            tabela.append({"cenario": aj["nome"], "verificacao": "INVIAVEL OU NAO RODADO"})
            continue
        r = json.load(open(caminho, encoding="utf-8"))
        extra = {}
        if "referencia_lexicografica" in r:
            extra = {"ref_" + k: v for k, v in r["referencia_lexicografica"]["custos_anuais"].items()}
        tabela.append(dict(cenario=aj["nome"], **{k: v for k, v in r["situacao_atual"].items()},
                           **r["indicadores"], **r["financeiro"], **r.get("custos_anuais", {}), **extra))
    validas = [t for t in tabela if "populacao_total" in t]
    cols = []
    for t in validas:
        cols += [c for c in t if c not in cols and c not in ("verificacao", "status")]
    cols.append("verificacao")
    with open(os.path.join(RESULTADOS, arquivo), "w", encoding="utf-8-sig") as f:
        f.write(";".join(cols) + "\n")
        for t in tabela:
            f.write(";".join(str(t.get(c, "")) for c in cols) + "\n")
    print("OK", arquivo, len(validas), "cenarios")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "cenarios":           # C1 (com a curva e a linha de base), C2 e C3
        for aj in CENARIOS:     # a curva do C1 e feita depois, com "python resolver.py curva"
            roda_cenario(aj)
        monta_tabela(CENARIOS, "cenarios.csv")
    elif args and args[0] == "atual":
        inst = instancia({"nome": PRINCIPAL})
        salva_atual(inst, *situacao_atual(inst))
        print("OK atual")
    elif args and args[0] == "conferir":          # verificacao independente das solucoes gravadas (segundos)
        conferir()
    elif args and args[0] == "dat":               # arquivos .dat do GLPK
        gera_dats()
    elif args and args[0] == "repolir":           # depois do integrado: polimento a partir da melhor designacao
        for aj in CENARIOS:
            repolir(aj)
    elif args and args[0] == "tabelas":
        monta_tabela(CENARIOS, "cenarios.csv")
        monta_tabela(SENSIBILIDADE, "sensibilidade.csv")
        monta_tabela(INTEGRADO, "integrado.csv")
    elif args and args[0] == "curva":             # curva de cobertura do C1 (setores fracionaveis: aproximacao rapida)
        inst = instancia({"nome": PRINCIPAL, "fracionado": True})
        caminho = os.path.join(RESULTADOS, PRINCIPAL, "resumo.json")
        res = json.load(open(caminho, encoding="utf-8"))
        n1 = res["indicadores"]["novas_equipes"]
        tot = res["indicadores"]["populacao_total"]
        pontos_curva, ini = [], None
        for k in list(range(0, math.ceil(n1))) + [n1]:
            rc = resolve_etapa(inst, {"ETAPA": 0, "OBRIG": 0, "LIM_EQ": k + 1e-6}, ini, tempo=600)
            a_c, _, _ = le_solucao(rc["valores"])
            cob = sum(inst["pop"][i] * f for i, fr in a_c.items() for f in fr.values())
            pontos_curva.append({"novas_equipes": k, "populacao_coberta": round(cob),
                                 "cobertura_efetiva_pct": round(100 * cob / tot, 2), "status": rc["status"], "gap": rc["gap"]})
            print(f"  Curva: {k:5} novas equipes -> {pontos_curva[-1]['cobertura_efetiva_pct']:6.2f}% ({rc['status']})", flush=True)
            ini = rc["valores"]
        res["etapas"]["curva_cobertura"] = pontos_curva
        res["etapas"]["curva_nota"] = "curva com setores fracionaveis (relaxacao da designacao unica), aproximacao do C1"
        json.dump(res, open(caminho, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        with open(os.path.join(RESULTADOS, PRINCIPAL, "curva.csv"), "w", encoding="utf-8-sig") as f:
            f.write(";".join(pontos_curva[0]) + "\n")
            for ln in pontos_curva:
                f.write(";".join(str(v) for v in ln.values()) + "\n")
        print("OK curva")
    elif args and args[0] == "integrado":         # requer C1, C2 e C3 ja resolvidos (referencia e inicio)
        for aj in INTEGRADO:
            roda_integrado(aj)
        monta_tabela(INTEGRADO, "integrado.csv")
    elif args and args[0] == "sensibilidade":      # variantes do C1 (o proprio C1 vem de "cenarios")
        for aj in SENSIBILIDADE:
            if aj["nome"] != PRINCIPAL:
                roda_cenario(aj)
        monta_tabela(SENSIBILIDADE, "sensibilidade.csv")
    else:
        nome = args[0] if args and not args[0].startswith("--") else PRINCIPAL
        aj = next((a for a in INTEGRADO if a["nome"] == nome), None)
        if aj:
            roda_integrado(aj)
        else:
            aj = next((a for a in CENARIOS + SENSIBILIDADE if a["nome"] == nome), {"nome": nome})
            roda_cenario(aj, curva="--curva" in args)
