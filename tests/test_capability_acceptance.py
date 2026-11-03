from copy import deepcopy
import hashlib

from app.services.capability_acceptance import CHECKS, RELEASE_CHECKS, assess


def fixture(tmp_path):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_owner_flow.py').write_text('def test_duplicate_has_one_effect(): pass\n')
    report=b'<testsuite><testcase classname="tests.test_owner_flow" name="test_duplicate_has_one_effect"/></testsuite>'
    (tmp_path/'run.xml').write_bytes(report)
    inventory={'source_commit':'original','routes':[{'method':'GET','path':'/old','status':'superseded'}],
        'skills':[{'name':'old_tool','status':'verified_local_mocked'}]}
    proof={'id':'native-replacement','originals':['route:GET /old','skill:old_tool'],
        'native_tree_sha256':'current', 'baseline_workflow':'Original owner outcome',
        'native_workflow':'Native implementation of the same owner outcome',
        'improvement':'A duplicate input has exactly one durable effect',
        'external_dependencies':['isolated-service'], 'live_verified':True,
        'evidence':['tests/test_owner_flow.py::test_duplicate_has_one_effect'],
        'test_run':{'status':'passed','failed':0,'skipped':0,'artifact':'run.xml','artifact_sha256':hashlib.sha256(report).hexdigest()},
        **{key:True for key in CHECKS}}
    acceptance={'source_commit':'original','workflows':[proof],
        'release_checks':{key:True for key in RELEASE_CHECKS}}
    return inventory,acceptance


def test_registration_and_superseded_labels_cannot_establish_completion(tmp_path):
    inventory,acceptance=fixture(tmp_path)
    acceptance['workflows']=[]
    report=assess(inventory,acceptance,root=tmp_path,native_fingerprint='current',artifacts_root=tmp_path)
    assert not report['complete'] and report['accepted_coverage_points']==0
    assert set(report['unaccepted_originals'])=={'route:GET /old','skill:old_tool'}


def test_one_verified_workflow_can_replace_multiple_original_interfaces(tmp_path):
    inventory,acceptance=fixture(tmp_path)
    report=assess(inventory,acceptance,root=tmp_path,native_fingerprint='current',artifacts_root=tmp_path)
    assert report['complete'] and report['accepted_coverage_points']==2


def test_stale_missing_ambiguous_or_unrun_evidence_keeps_acceptance_open(tmp_path):
    inventory,acceptance=fixture(tmp_path)
    for patch in ({'native_tree_sha256':'stale'},{'improvement':''},
                  {'evidence':['tests/test_owner_flow.py::test_nonexistent']},
                  {'evidence':['../private.py::test_secret']},
                  {'test_run':{'status':'passed','failed':0,'skipped':1}},
                  {'live_verified':False},{'originals':['skill:invented_tool']},
                  {'owner_flow_verified':False}):
        changed=deepcopy(acceptance); changed['workflows'][0].update(patch)
        report=assess(inventory,changed,root=tmp_path,native_fingerprint='current',artifacts_root=tmp_path)
        assert not report['complete'] and report['invalid_workflows']
    changed=deepcopy(acceptance);changed['source_commit']='other-baseline'
    assert not assess(inventory,changed,root=tmp_path,native_fingerprint='current')['complete']


def test_deferred_work_and_release_acceptance_are_visible_not_silently_removed(tmp_path):
    inventory,acceptance=fixture(tmp_path)
    acceptance['release_checks']={}
    report=assess(inventory,acceptance,root=tmp_path,native_fingerprint='current',artifacts_root=tmp_path)
    assert report['accepted_coverage_points']==2 and not report['complete']
    assert set(report['release_checks_pending'])==set(RELEASE_CHECKS)


def test_a_claimed_test_pass_does_not_substitute_for_an_executed_matching_case(tmp_path):
    inventory,acceptance=fixture(tmp_path)
    assert not assess(inventory,acceptance,root=tmp_path,native_fingerprint='current')['complete']
    for xml in (b'<testsuite/>',b'<testsuite><testcase classname="tests.test_owner_flow" name="other"/></testsuite>',
        b'<testsuite><testcase classname="tests.test_owner_flow" name="test_duplicate_has_one_effect"><skipped/></testcase></testsuite>'):
        (tmp_path/'run.xml').write_bytes(xml)
        acceptance['workflows'][0]['test_run']['artifact_sha256']=hashlib.sha256(xml).hexdigest()
        assert not assess(inventory,acceptance,root=tmp_path,native_fingerprint='current',artifacts_root=tmp_path)['complete']
