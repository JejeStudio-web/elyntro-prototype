def test_studio_status_and_scene_validation():
    import app as mod
    c=mod.app.test_client()
    assert c.get('/api/studio/status').status_code==200
    scene={'title':'test','background':'#101421','camera':{'position':[4,3,6],'lookAt':[0,0,0],'fov':50},'objects':[{'type':'sphere','name':'ball','position':[0,0,0],'rotation':[0,0,0],'scale':[1,1,1],'color':'#8295f5'}]}
    r=c.post('/api/studio/scene/validate',json={'scene':scene})
    assert r.status_code==200 and r.get_json()['object_count']==1

def test_scene_rejects_unknown_code_fields():
    import app as mod
    c=mod.app.test_client()
    assert c.post('/api/studio/scene/validate',json={'scene':{'objects':[],'script':'alert(1)'}}).status_code==400

def test_founder_mode_closed_by_default(monkeypatch):
    import app as mod
    monkeypatch.delenv('ELYNTRO_FOUNDER_TOKEN',raising=False)
    assert mod.app.test_client().post('/api/studio/founder/proposals',json={'title':'x','rationale':'y','patch':'z'}).status_code==503

def test_founder_proposal_approval_never_publishes(monkeypatch):
    import app as mod
    monkeypatch.setenv('ELYNTRO_FOUNDER_TOKEN','test-founder-token')
    c=mod.app.test_client(); h={'Authorization':'Bearer test-founder-token'}
    assert c.post('/api/studio/founder/proposals',json={'title':'x','rationale':'y','patch':'z'}).status_code==401
    r=c.post('/api/studio/founder/proposals',headers=h,json={'title':'x','rationale':'y','patch':'z'})
    assert r.status_code==201
    item=r.get_json()['proposal']; assert item['published'] is False
    a=c.post('/api/studio/founder/proposals/'+item['id']+'/approve',headers=h)
    assert a.status_code==200 and a.get_json()['proposal']['published'] is False

def test_comfy_requires_founder_auth(monkeypatch):
    import app as mod
    monkeypatch.setenv('ELYNTRO_FOUNDER_TOKEN','test-founder-token')
    assert mod.app.test_client().post('/api/studio/comfy/generate',json={'prompt':'test'}).status_code==401
