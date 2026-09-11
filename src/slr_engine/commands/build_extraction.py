"""
Rebuild 10_data_extraction_<date>.csv from the latest 09_fulltext_review_queue CSV.

Usage:
    python -m commands.build_extraction [--queue data/snowball_output/05_review/09_fulltext_review_queue_2026-05-02.csv]
                                        [--out   data/snowball_output/06_extraction/10_data_extraction_2026-05-02.csv]

Run this any time after adding new papers to the queue CSV
(new rows need fulltext_decision=INCLUDE or abstract_2nd_pass_decision=INCLUDE).
"""

import argparse, csv, re
from collections import Counter
from pathlib import Path

# Reads the full-text queue from 05_review/ and writes the extraction table to 06_extraction/.
from slr_engine.config import REVIEW_DIR, EXTRACTION_DIR

DEFAULT_QUEUE = str(REVIEW_DIR / '09_fulltext_review_queue_2026-05-02.csv')
DEFAULT_OUT   = str(EXTRACTION_DIR / '10_data_extraction_2026-05-02.csv')

PEFT_WORDS = ['lora','peft','adapter','fine-tun','finetun','parameter-efficient','param-efficient']

def get_notes(r): return (r.get('fulltext_notes','') or r.get('abstract_2nd_pass_notes','')).strip()
def get_sections(notes): return re.findall(r'§\d+\.\w+', notes)
def get_stars(notes): return re.findall(r'★\w+', notes)

def classify_corpus(r):
    notes    = get_notes(r).lower()
    secs     = get_sections(get_notes(r))
    stars    = get_stars(get_notes(r))
    title    = r['title'].lower()
    abstract = (r.get('abstract','') or '').lower()
    combined = notes + ' ' + title + ' ' + abstract[:400]

    if stars: return 'core'
    s = ' '.join(secs)
    h3,h4,h5,h6,h7 = ('§3' in s),('§4' in s),('§5' in s),('§6' in s),('§7' in s)

    if h4: return 'core'
    if h7 and any(w in combined for w in PEFT_WORDS): return 'core'
    if h6 and any(w in combined for w in PEFT_WORDS): return 'core'
    if h5 and any(w in combined for w in ['lora','multi-adapter','adapter','multi-tenant','co-serv','many-adapter','multi-lora']): return 'core'
    if h3 and not h4 and not h5 and not h6 and not h7:
        return 'core' if any(w in combined for w in ['composition','fusion','modular','routing','mixture']) else 'background'
    if h5 and not h4 and not h6:
        return 'core' if any(w in combined for w in ['lora','adapter','peft','multi-tenant']) else 'background'
    if h6 and not any(w in combined for w in PEFT_WORDS): return 'background'
    if '§2' in s and not h4 and not h5 and not h6: return 'background'
    if h7 and not any(w in combined for w in PEFT_WORDS): return 'background'
    if not secs:
        peft = any(w in combined for w in PEFT_WORDS)
        dist = any(w in combined for w in ['federat','p2p','peer','distributed','decentrali','gossip'])
        return 'core' if (peft and dist) else 'background'
    return 'core'

def infer_contribution_type(r):
    notes    = get_notes(r).lower()
    title    = r['title'].lower()
    abstract = (r.get('abstract','') or '').lower()
    txt      = notes + ' ' + title + ' ' + abstract[:300]
    secs     = ' '.join(get_sections(get_notes(r)))

    if any(w in txt for w in ['survey','review','overview','guide to','taxonomy','comprehensive package']): return 'survey'
    if any(w in txt for w in ['kademlia','dht','xor metric']): return 'foundational-P2P'
    if 'gossip learning' in txt or ('gossip' in txt and 'linear model' in txt): return 'gossip-learning'
    if any(w in txt for w in ['hypernetwork','hyper-adapter','hypernet','hyperadapter','hyperpelt','hyperdecoders']): return 'adapter-composition'
    if any(w in txt for w in ['zero-shot routing','tokenwise gating','route among','phatgoose','single-dataset expert','cross-task generali','learning to route','route among specialized','expert collection','recycle','parameter-efficient fine-tuning.*expert']): return 'adapter-routing'
    if 'continual' in txt and any(w in txt for w in ['adapter','peft','lora']): return 'adapter-composition'
    if any(w in txt for w in ['adapter search','searching efficient adapter','hierarchical search for efficient']): return 'adapter-composition'
    if any(w in txt for w in ['distributed peft','cloud-device','kill-and-revive']): return 'distributed-PEFT'
    if any(w in txt for w in ['muxtune','mux']) and any(w in txt for w in ['peft','adapter','lora','multi-task']): return 'adapter-serving'
    if any(w in txt for w in ['peft transfer','trans-peft','transferable peft']): return 'PEFT-method'
    # Fed-tuning mobile/web without explicit PEFT keywords in short notes
    if any(w in txt for w in ['fed-tun','fed tuning','fedtun']):
        return 'federated-PEFT'
    if 'federat' in txt and any(w in txt for w in ['fine-tun','finetun']):
        return 'federated-PEFT'
    if 'federat' in txt and any(w in txt for w in PEFT_WORDS):
        if any(w in txt for w in ['hotswap','routing','expert','switch','moe']): return 'federated-PEFT+routing'
        if any(w in txt for w in ['privacy','differentially private','dp-']): return 'federated-PEFT+privacy'
        return 'federated-PEFT'
    if any(w in txt for w in ['moe','mixture-of-expert','mixture of expert','mixture-of-lora','mixture of lora','mixture-of-adapter','sparser mixture','mixture of low-rank']) and any(w in txt for w in ['adapter','lora','peft']): return 'MoE-adapter-routing'
    if any(w in txt for w in ['moe','mixture-of-expert','switch transformer']): return 'MoE-routing'
    if any(w in txt for w in ['multi-head adapter','adapter routing','cross-task']) and 'adapter' in txt: return 'adapter-routing'
    if any(w in txt for w in ['composition','composit','adamix','lorahub','fusion','modular','soup','unipelt','unified framework']) and any(w in txt for w in ['adapter','lora','peft']): return 'adapter-composition'
    if any(w in txt for w in ['co-serv','multi-tenant','many-adapter','multi-lora','multi-adapter','slo','scalable serv','concurrent serv']) and any(w in txt for w in ['lora','adapter']): return 'adapter-serving'
    if 'serverless' in txt and any(w in txt for w in ['lora','adapter']): return 'adapter-serving'
    if any(w in txt for w in ['qlora','relora']) and '§3' in secs: return 'PEFT-method'
    if any(w in txt for w in ['lora','low-rank adaptation']) and '§3' in secs: return 'PEFT-method'
    if 'adapter' in txt and '§3' in secs: return 'PEFT-method'
    if 'p2p' in txt or 'peer-to-peer' in txt: return 'P2P-FL' if 'federat' in txt else 'P2P-DL'
    if any(w in txt for w in ['federat','decentrali','gossip']): return 'P2P-FL'
    if any(w in txt for w in ['distributed inference','edge infer','collaborative infer','model shard','sharding']): return 'distributed-inference'
    if any(w in txt for w in ['serving','inference']) and any(w in txt for w in ['lora','adapter']): return 'adapter-serving'
    if any(w in txt for w in ['serving','inference','edge deploy']): return 'distributed-inference'
    # Prefix/prompt tuning methods without section tags (abstract-2nd-pass papers)
    if any(w in txt for w in ['prefix-tuning','prefix tuning','prompt tuning','p-tuning','adaptive prefix']): return 'PEFT-method'
    if any(w in txt for w in ['empirical analysis','strengths and weaknesses','better and cheaper']) and 'peft' in txt: return 'survey'
    return 'other'

def infer_peft_technique(r):
    txt = (get_notes(r) + ' ' + r['title'] + ' ' + (r.get('abstract','') or '')).lower()
    techs = []
    if 'qlora' in txt: techs.append('QLoRA')
    elif 'lora' in txt or 'low-rank adaptation' in txt: techs.append('LoRA')
    if 'houlsby' in txt or ('bottleneck' in txt and 'adapter' in txt): techs.append('Houlsby-adapter')
    if 'prefix tun' in txt: techs.append('prefix-tuning')
    if 'prompt tun' in txt: techs.append('prompt-tuning')
    if 'compacter' in txt: techs.append('Compacter')
    if 'hypercomplex' in txt: techs.append('hypercomplex')
    if ('moe' in txt or 'mixture' in txt) and ('adapter' in txt or 'lora' in txt): techs.append('MoE-adapters')
    if 'hypernetwork' in txt or 'hypernet' in txt: techs.append('hypernetwork')
    if not techs and any(w in txt for w in ['peft','adapter','fine-tun','finetun']): techs.append('generic-PEFT')
    return '; '.join(techs) if techs else 'none'

def infer_distribution(r):
    txt = (get_notes(r) + ' ' + r['title'] + ' ' + (r.get('abstract','') or '')).lower()
    mechs = []
    if 'kademlia' in txt or 'dht' in txt: mechs.append('DHT')
    if 'gossip' in txt: mechs.append('gossip')
    if 'blockchain' in txt: mechs.append('blockchain')
    if 'split learn' in txt: mechs.append('split-learning')
    if 'p2p' in txt or 'peer-to-peer' in txt: mechs.append('P2P')
    if 'federat' in txt: mechs.append('federated')
    if 'decentrali' in txt and 'federat' not in txt: mechs.append('decentralised')
    if any(w in txt for w in ['model parallel','tensor parallel','pipeline parallel']): mechs.append('model-parallel')
    if 'serverless' in txt: mechs.append('serverless')
    if 'edge' in txt and any(w in txt for w in ['collaborat','distribut','shard','offload']): mechs.append('edge-collaborative')
    if not mechs: mechs.append('none')
    return '; '.join(dict.fromkeys(mechs))

def extract_key_finding(r):
    notes = get_notes(r).strip()
    if not notes: return (r.get('abstract','') or '').strip()[:200]
    clean = re.sub(r'§\d+\.\w+\s*', '', notes)
    clean = re.sub(r'★\w+\s*', '', clean).strip()
    for p in re.split(r'[.;]', clean):
        p = p.strip()
        if len(p) > 20: return p
    return clean[:200]

SORT_ORDER = {
    'adapter-serving':0,'federated-PEFT':1,'federated-PEFT+routing':2,'federated-PEFT+privacy':3,
    'MoE-adapter-routing':4,'adapter-routing':5,'adapter-composition':6,'distributed-PEFT':7,
    'P2P-FL':8,'P2P-DL':9,'survey':10,'PEFT-method':20,'distributed-inference':21,
    'MoE-routing':22,'gossip-learning':23,'foundational-P2P':24,'other':99,
}

FIELDNAMES = ['paper_key','arxiv_id','doi','title','year','venue','citation_count',
              'tier','review_source','corpus','contribution_type','method_name',
              'peft_technique','distribution_mechanism','thesis_sections',
              'contribution_codes','datasets','metrics','key_finding','notes_raw']

def build(queue_path: str, out_path: str):
    rows = list(csv.DictReader(open(queue_path, encoding='utf-8')))
    included = [r for r in rows
                if r.get('fulltext_decision','').strip() == 'INCLUDE'
                or r.get('abstract_2nd_pass_decision','').strip() == 'INCLUDE']
    print(f'Included papers: {len(included)}')

    extraction = []
    for r in included:
        notes   = get_notes(r)
        corpus  = classify_corpus(r)
        sections = '; '.join(get_sections(notes)) or ''
        stars    = '; '.join(get_stars(notes)) or ''
        source   = 'fulltext' if r.get('fulltext_decision','').strip() == 'INCLUDE' else 'abstract-2nd-pass'
        extraction.append({
            'paper_key':              (r.get('arxiv_id') or r.get('doi') or r.get('ss_paper_id') or '').strip(),
            'arxiv_id':               r.get('arxiv_id','').strip(),
            'doi':                    r.get('doi','').strip(),
            'title':                  r['title'].strip(),
            'year':                   r.get('year','').strip(),
            'venue':                  r.get('venue','').strip(),
            'citation_count':         r.get('citation_count','').strip(),
            'tier':                   r.get('tier','').strip() or r.get('seed_group','').strip(),
            'review_source':          source,
            'corpus':                 corpus,
            'contribution_type':      infer_contribution_type(r),
            'peft_technique':         infer_peft_technique(r),
            'distribution_mechanism': infer_distribution(r),
            'thesis_sections':        sections,
            'contribution_codes':     stars,
            'key_finding':            extract_key_finding(r),
            'method_name':            '',
            'datasets':               '',
            'metrics':                '',
            'notes_raw':              notes,
        })

    extraction.sort(key=lambda e: (
        0 if e['corpus'] == 'core' else 1,
        SORT_ORDER.get(e['contribution_type'], 50),
        -(int(e['year'] or 0)),
    ))

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction='ignore')
        w.writeheader()
        w.writerows(extraction)

    core = [e for e in extraction if e['corpus'] == 'core']
    bg   = [e for e in extraction if e['corpus'] == 'background']
    print(f'Core: {len(core)}, Background: {len(bg)}')
    print('\nCore contribution types:')
    for k, v in sorted(Counter(e['contribution_type'] for e in core).items(), key=lambda x: -x[1]):
        print(f'  {k}: {v}')
    any_other = [e for e in extraction if e['contribution_type'] == 'other']
    if any_other:
        print(f'\nWARNING: {len(any_other)} papers still classified as "other" — review manually:')
        for e in any_other:
            print(f'  [{e["corpus"]}] {e["title"][:70]}')
    print(f'\nWritten: {out_path}')

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue', default=DEFAULT_QUEUE)
    p.add_argument('--out',   default=DEFAULT_OUT)
    args = p.parse_args()
    build(args.queue, args.out)
