"""Original interfaces are coverage points; owner workflows establish parity.

Registration, migration labels and test-file names alone never pass this gate.
One verified workflow may cover several original interfaces without restoring
their old implementation. Every useful outcome still requires explicit proof.
"""
import ast
import hashlib
from pathlib import Path
from xml.etree import ElementTree


CHECKS = ('outcomes_preserved', 'owner_flow_verified', 'deterministic_effects_verified',
          'configuration_and_recovery_verified', 'behavior_verified')
RELEASE_CHECKS = ('projects_and_canvas_accepted', 'migration_and_rollback_verified',
                  'live_end_to_end_verified')


def fingerprint(hashes):
    payload = '\n'.join(f'{path}:{value}' for path,value in sorted(hashes.items()))
    return hashlib.sha256(payload.encode()).hexdigest()


def originals(inventory):
    return ({'route:' + row['method'] + ' ' + row['path'] for row in inventory['routes']}
            | {'skill:' + row['name'] for row in inventory['skills']})


def assess(inventory, acceptance, *, root, native_fingerprint, artifacts_root=None):
    required = originals(inventory)
    covered = set()
    invalid = []
    baseline_matches = acceptance.get('source_commit') == inventory['source_commit']
    cached_cases = {}
    for index, proof in enumerate(acceptance.get('workflows', [])):
        reasons = []
        points = set(proof.get('originals', []))
        if not baseline_matches: reasons.append('original_baseline_changed')
        if not points or points-required: reasons.append('unknown_or_missing_originals')
        if proof.get('native_tree_sha256') != native_fingerprint: reasons.append('stale_native_evidence')
        if any(proof.get(key) is not True for key in CHECKS): reasons.append('unverified_outcome_checks')
        if not all(isinstance(proof.get(key),str) and proof[key].strip()
                   for key in ('baseline_workflow','native_workflow','improvement')):
            reasons.append('missing_workflow_comparison')
        if not isinstance(proof.get('external_dependencies'),list): reasons.append('external_dependencies_not_declared')
        if proof.get('external_dependencies') and proof.get('live_verified') is not True:
            reasons.append('external_dependencies_unverified')
        evidence = proof.get('evidence', [])
        if not evidence: reasons.append('missing_behavioral_evidence')
        for item in evidence:
            if not isinstance(item,str) or '::' not in item:
                reasons.append('unnamed_behavioral_case'); continue
            filename, case = item.split('::',1)
            path = (Path(root)/filename).resolve()
            tests = (Path(root)/'tests').resolve()
            if not path.is_relative_to(tests) or path.suffix != '.py' or not path.is_file():
                reasons.append('invalid_evidence_path'); continue
            if path not in cached_cases:
                tree = ast.parse(path.read_text(encoding='utf-8-sig'))
                cached_cases[path] = {node.name for node in ast.walk(tree)
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_')}
            if case not in cached_cases[path]: reasons.append('behavioral_case_not_found')
        run = proof.get('test_run', {})
        if run.get('status') != 'passed' or run.get('failed') != 0 or run.get('skipped') != 0 or not run.get('artifact_sha256'):
            reasons.append('missing_passed_run_evidence')
        # A claimed pass or test filename is not execution evidence. Verify the
        # retained report bytes and the actual named cases before accepting it.
        try:
            if artifacts_root is None: raise ValueError()
            report_path = (Path(artifacts_root)/run.get('artifact','')).resolve()
            if not report_path.is_relative_to(Path(artifacts_root).resolve()): raise ValueError()
            raw = report_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != run['artifact_sha256']: raise ValueError()
            tree = ElementTree.fromstring(raw)
            cases = list(tree.iter('testcase'))
            if not cases or any(list(c.iter('failure')) or list(c.iter('error')) or list(c.iter('skipped')) for c in cases):
                raise ValueError()
            executed = {(c.get('classname'),c.get('name','').split('[',1)[0]) for c in cases}
            for item in evidence:
                filename,name = item.split('::',1)
                module = filename.removesuffix('.py').replace('/','.').replace('\\','.')
                if (module,name) not in executed: raise ValueError()
        except (ValueError,OSError,KeyError,TypeError,ElementTree.ParseError):
            reasons.append('run_artifact_missing_invalid_or_not_matching_cases')
        if reasons:
            invalid.append({'workflow': proof.get('id',str(index)), 'reasons': sorted(set(reasons))})
        else:
            covered.update(points)
    missing = sorted(required-covered)
    release_missing = [key for key in RELEASE_CHECKS if acceptance.get('release_checks',{}).get(key) is not True]
    return {'target':'Superior Mark VI', 'complete':baseline_matches and not missing and not invalid and not release_missing,
            'baseline_matches':baseline_matches, 'native_tree_sha256':native_fingerprint,
            'coverage_points':len(required), 'accepted_coverage_points':len(covered),
            'unaccepted_originals':missing, 'invalid_workflows':invalid,
            'release_checks_pending':release_missing,
            'interpretation':'Coverage points are original interfaces, not a count of missing product features. Workflow proof establishes acceptance.'}
