#!/usr/bin/env python3
"""Produce a VDB-backed reverse dependency source for the live install."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
import portage
from portage.dbapi.vartree import vardbapi
from portage.dep import use_reduce, paren_reduce

def canon(v): return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()

class DependencyChoiceError(ValueError):
    """The installed VDB does not identify which alternative Portage chose."""

    def __init__(self, message, *, operator=None, branch_count=None):
        super().__init__(message)
        self.operator = operator
        self.branch_count = branch_count

def load_choice_review(review):
    if not isinstance(review, dict) or not isinstance(review.get("records"), list) or not review["records"]:
        raise ValueError("dependency-choice review has invalid schema")
    choices = {}
    for record in review["records"]:
        key = (record.get("consumer_cpv"), record.get("field"), record.get("expression_sha256"))
        if (any(value is None for value in key)
                or record.get("choice_operator") not in {"||", "^^", "??"}
                or not isinstance(record.get("branch_count"), int)
                or not isinstance(record.get("selected_branch"), int)):
            raise ValueError("dependency-choice review record is incomplete")
        if key in choices:
            raise ValueError(f"duplicate dependency-choice review record: {key}")
        choices[key] = record
    return choices

def _collect(tree, matcher=None, choice_selector=None):
    if isinstance(tree, str):
        return [tree] if "/" in tree and not tree.startswith("!") else []
    if not isinstance(tree, list):
        return []
    if tree and isinstance(tree[0], str) and tree[0] in {"||", "^^", "??"}:
        branches = tree[1] if len(tree) == 2 and isinstance(tree[1], list) else tree[1:]
        if choice_selector is not None:
            selection = choice_selector(tree[0], branches)
            if isinstance(selection, dict):
                if (selection.get("choice_operator") != tree[0]
                        or selection.get("branch_count") != len(branches)):
                    raise DependencyChoiceError(
                        "dependency-choice review does not match the reduced expression",
                        operator=tree[0], branch_count=len(branches),
                    )
                selected_index = selection.get("selected_branch")
            else:
                selected_index = selection
            if not isinstance(selected_index, int) or not 0 <= selected_index < len(branches):
                raise DependencyChoiceError(
                    "dependency-choice review selected an invalid branch",
                    operator=tree[0], branch_count=len(branches),
                )
            return _collect(branches[selected_index], matcher, choice_selector)
        if matcher is None:
            raise DependencyChoiceError(
                f"unresolved Portage dependency choice operator {tree[0]!r}",
                operator=tree[0], branch_count=len(branches),
            )
        selected = []
        for branch in branches:
            branch_atoms = _collect(branch, matcher, choice_selector)
            if branch_atoms and any(matcher(atom) for atom in branch_atoms):
                selected.append(branch_atoms)
        if len(selected) != 1:
            raise DependencyChoiceError(
                f"Portage dependency choice {tree[0]!r} has {len(selected)} installed alternatives",
                operator=tree[0], branch_count=len(branches),
            )
        return selected[0]
    values = []
    for item in tree:
        values.extend(_collect(item, matcher, choice_selector))
    return values

def atoms(expr, useflags=(), matcher=None, choice_selector=None):
    if not expr: return []
    tree=use_reduce(paren_reduce(expr), uselist=useflags, flat=False)
    return _collect(tree, matcher, choice_selector)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--vdb',default='/var/db/pkg'); ap.add_argument('--choice-review',type=Path); ap.add_argument('--output',required=True); a=ap.parse_args()
    db=vardbapi()
    requested_vdb=Path(a.vdb).resolve()
    actual_vdb=Path(getattr(db,'dbroot',requested_vdb)).resolve()
    if actual_vdb != requested_vdb:
        raise SystemExit(f'REFUSED: Portage VDB root mismatch: requested={requested_vdb} actual={actual_vdb}')
    cpvs=sorted(db.cpv_all())
    runtime_rows=[]; build_rows=[]; source_hashes=[]; source_errors=[]
    choice_reviews={}; used_choice_reviews=set(); choice_review_sha256=None
    if a.choice_review:
        review=json.loads(a.choice_review.read_text())
        try:
            choice_reviews=load_choice_review(review)
        except ValueError as exc:
            raise SystemExit(f'REFUSED: {exc}')
        choice_review_sha256=hashlib.sha256(canon(review)).hexdigest()
    for cpv in cpvs:
        try: vals=db.aux_get(cpv,['DEPEND','RDEPEND','PDEPEND','BDEPEND','IDEPEND','USE','BUILD_TIME','CONTENTS','REPOSITORY','SLOT','SUBSLOT'])
        except Exception as exc:
            source_errors.append({'cpv':cpv,'stage':'consumer-metadata','error':str(exc)})
            continue
        deps=[]
        useflags=tuple(vals[5].split())
        for field,expr in zip(('DEPEND','RDEPEND','PDEPEND','BDEPEND','IDEPEND'), vals[:5]):
            source_hashes.append(hashlib.sha256((cpv+'\0'+field+'\0'+expr).encode()).hexdigest())
            try:
                review_key=(cpv,field,hashlib.sha256(expr.encode()).hexdigest())
                def selector(operator, branches, key=review_key):
                    if key not in choice_reviews:
                        raise DependencyChoiceError(f"no review record for dependency choice {key}")
                    used_choice_reviews.add(key)
                    return choice_reviews[key]
                evaluated = atoms(expr,useflags, matcher=db.match, choice_selector=selector if choice_reviews else None)
            except Exception as exc:
                error={'cpv':cpv,'field':field,'stage':'dependency-parse','error':str(exc)}
                if isinstance(exc, DependencyChoiceError):
                    error.update({
                        'expression_sha256': hashlib.sha256(expr.encode()).hexdigest(),
                        'choice_operator': exc.operator,
                        'choice_branch_count': exc.branch_count,
                    })
                source_errors.append(error)
                target = build_rows if field in {'DEPEND','BDEPEND','IDEPEND'} else runtime_rows
                target.append({'consumer_cpv':cpv,'relationship':'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime','evidence':{'field':field,'error':str(exc),'expression_sha256':hashlib.sha256(expr.encode()).hexdigest(),'choice_operator':getattr(exc, 'operator', None),'choice_branch_count':getattr(exc, 'branch_count', None)}})
                continue
            for atom in evaluated:
                try:
                    providers = sorted(db.match(atom))
                except Exception as exc:
                    source_errors.append({'cpv':cpv,'field':field,'atom':atom,'stage':'provider-match','error':str(exc)})
                    target = build_rows if field in {'DEPEND','BDEPEND','IDEPEND'} else runtime_rows
                    target.append({'consumer_cpv':cpv,'relationship':'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime','evidence':{'field':field,'atom':atom,'error':str(exc)}})
                    continue
                relation = 'portage-build' if field in {'DEPEND','BDEPEND','IDEPEND'} else 'portage-runtime'
                for provider in providers:
                    try:
                        provider_vals = db.aux_get(provider,['REPOSITORY','SLOT','SUBSLOT'])
                    except Exception as exc:
                        source_errors.append({'cpv':cpv,'provider_cpv':provider,'field':field,'stage':'provider-metadata','error':str(exc)})
                        continue
                    target = build_rows if relation == 'portage-build' else runtime_rows
                    target.append({'provider_cpv':provider,'consumer_cpv':cpv,'relationship':relation,'evidence':{'vdb_cpv':cpv,'field':field,'atom':atom,'evaluated_atom':atom,'useflags':sorted(useflags),'provider_repository':provider_vals[0],'provider_slot':provider_vals[1],'provider_subslot':provider_vals[2]}})
    if choice_reviews:
        unused=sorted(set(choice_reviews)-used_choice_reviews)
        if unused:
            raise SystemExit(f"REFUSED: dependency-choice review contains unused records: {unused[:3]}")
    def unique(rows):
        return sorted({(x.get('provider_cpv'),x['consumer_cpv'],x['relationship'],json.dumps(x.get('evidence',{}),sort_keys=True)):x for x in rows}.values(),key=lambda x:(x.get('provider_cpv',''),x['consumer_cpv'],x['relationship']))
    out={'record_type':'live-portage-dependency-source','schema_version':2,'vdb_root':str(Path(a.vdb).resolve()),'cpv_count':len(cpvs),'source_digest':hashlib.sha256(canon(sorted(source_hashes))).hexdigest(),'choice_review_sha256':choice_review_sha256,'source_errors':source_errors,'records':unique(runtime_rows),'build_records':unique(build_rows)}
    out['sha256']=hashlib.sha256(canon(out)).hexdigest(); Path(a.output).write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    if source_errors:
        raise SystemExit(f"REFUSED: Portage dependency source contains {len(source_errors)} metadata/parse errors; see {a.output}")
    print(json.dumps({'cpvs':len(cpvs),'records':len(out['records']),'sha256':out['sha256']}))
if __name__=='__main__': main()
