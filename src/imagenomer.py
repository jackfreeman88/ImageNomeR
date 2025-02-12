import uuid
import json
import requests
import re
import threading
import numpy as np
import re
import sys
import matplotlib.pyplot as plt
import nilearn.plotting as plotting
import io
import base64

from power264 import power_rois

'''
ImageNomeR Library

User imports this into their python code, creates an Analysis object, then creates
JsonData, JsonSubjects, or JsonMetadata objects and sends them to the server via .post()

Server then sends them on request to client on the web browser
'''

class JsonBase:
    '''
    Base for JsonData, JsonSubjects, and JsonMetadata

    .update() folds a user-defined dict into the created JSON
    .pack() methods on child classes validate correct fields, and inputting
    values that the json library can't handle will raise an Exception
    '''
    def __init__(self, analysis):
        assert isinstance(analysis, Analysis)
        self.analysis = analysis
        self.dict = {}

    def update(self, dct):
        self.dict.update(dct)

class JsonSubjects(JsonBase):
    def __init__(self, analysis):
        super().__init__(analysis)

    def pack(self):
        keys = set(self.dict.keys())
        assert 'IDs' in keys
        assert 'Features' in keys
        assert any(key in keys for key in ['Responses', 'Groups'])
        return json.dumps(self.dict)

    def post(self):
        return self.analysis.postSubjects(self.pack())

class JsonData(JsonBase):
    def __init__(self, analysis):
        super().__init__(analysis)

    def pack(self, runid):
        self.dict['runid'] = runid
        self.dict['desc'] = self.analysis.desc
        keys = set(self.dict.keys())
        assert any(key in keys for key in ['Accuracy', 'RMSE'])
        assert 'Train' in keys and 'Test' in keys 
        assert len(self.dict['Train']) == len(self.dict['Test'])
        assert len(self.dict['Train']) >= 2
        assert 'Weights' in keys and 'Labels' in keys
        assert len(self.dict['Weights']) == len(self.dict['Labels'])
        if len(self.analysis.prev) > 0:
            assert self.analysis.prev[0]['Labels'] == self.dict['Labels']
        if 'Error' in keys and self.analysis.subjects is not None:
            assert isinstance(self.dict['Error'], dict) and set(self.dict['Error'].keys()) == self.analysis.subjects
        return json.dumps(self.dict)

    def post(self):
        runid = self.analysis.runid
        return self.analysis.postData(self.pack(runid))

class JsonMetadata(JsonBase):
    def __init__(self, analysis):
        super().__init__(analysis)
        assert len(analysis.prev) > 0, 'Load a run before adding metadata'

    def post(self):
        return self.analysis.postMetadata(self.pack())

class JsonGeneMetadata(JsonMetadata):
    def __init__(self, analysis):
        super().__init__(analysis)

    def pack(self):
        return json.dumps(self.dict)

class JsonFCMetadata(JsonMetadata):
    '''
    Idea here is that connections in the form of xxx-xxx are mapped to brain functional networks
    1-14, determined by xxx

    Not too much enforced here, except xxx-xxx format
    '''
    def __init__(self, analysis):
        super().__init__(analysis)
        pat = re.compile('\d+-\d+')
        assert all(pat.fullmatch(label) for label in self.analysis.prev[0]['Labels'])   
    
    def pack(self):
        return json.dumps(self.dict)

def toB64String(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

class JsonImage(JsonBase):
    '''
    Client can request server to draw an image of regions or connections using nilearn.plotting
    '''
    def __init__(self, analysis, clientReq):
        super().__init__(analysis)
        if 'regions' in clientReq:
            regs = clientReq['regions']
            nodeCoords = np.zeros((len(regs),3))
            for i,reg in enumerate(regs):
                nodeCoords[i] = power_rois[reg]
            fig, ax = plt.subplots()
            plotting.plot_markers(np.ones(nodeCoords.shape[0]), nodeCoords, figure=fig, axes=ax)
            self.dict = {
                'b64': toB64String(fig),
            }
        elif 'connections' in clientReq:
            cons = clientReq['connections']
            Amap = {}
            count = 0
            for con in cons:
                for i in range(2):
                    if con[i] not in Amap:
                        Amap[con[i]] = count
                        count += 1
            A = np.zeros((count, count))
            for con in cons:
                A[Amap[con[0]], Amap[con[1]]] = 1
            A = A + A.T
            nodeCoords = np.zeros((count,3))
            for reg in Amap.keys():
                nodeCoords[Amap[reg]] = power_rois[reg]
            fig, ax = plt.subplots()
            plotting.plot_connectome(A, nodeCoords, node_color='blue', figure=fig, axes=ax)
            self.dict = {
                'b64': toB64String(fig),
            }
        else:
            raise Exception('Bad client request type')
        
    def pack(self):
        return json.dumps(self.dict)
        
    def post(self):
        return self.analysis.postImage(self.pack())

class Analysis:
    '''
    Analysis class holds description of analysis and domain of server
    It also keeps track of the unique analysis ID

    .postData() .postMetadata() and .postSubjects() are called by the .post()
    methods of JsonBase-derived classes
    '''
    instances = []

    def __init__(self, desc='test analysis', host='localhost', port=80, kill_others=True):
        self.id = uuid.uuid4()
        self.desc = desc
        self.host = host
        self.prev = []
        self.runid = 0
        self.subjects = None
        self.port = port
        self.headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        self.fail = 0
        self.t = None
        self.lock = threading.Lock()
        Analysis.instances.append(self)
        self.poll()
        if kill_others:
            self.kill_others()
    
    @staticmethod
    def kill_analysis(an):
        got = an.lock.acquire(timeout=1)
        if an.t is not None:
            an.t.cancel()
            an.t = None
        if got:
            an.lock.release()

    def kill_others(self):
        for an in Analysis.instances:
            if an == self:
                continue
            Analysis.kill_analysis(an)

    def kill_self(self):
        Analysis.kill_analysis(self)
    
    def postData(self, data):
        url = f'http://{self.host}:{self.port}/post?id={self.id}&runid={self.runid}&type=data'
        r = requests.post(url, headers=self.headers, data=data);
        if r.content.decode() == 'Success':
            self.prev.append(json.loads(data))
            self.runid += 1
        return r

    def postMetadata(self, meta):
        url = f'http://{self.host}:{self.port}/post?id={self.id}&type=metadata'
        r = requests.post(url, headers=self.headers, data=meta);
        return r

    def postSubjects(self, subjects):
        url = f'http://{self.host}:{self.port}/post?id={self.id}&type=subjects'
        r = requests.post(url, headers=self.headers, data=subjects)
        if r.content.decode() == 'Success':
            self.subjects = set(json.loads(subjects))
        return r
    
    def postImage(self, image):
        url = f'http://{self.host}:{self.port}/post?id={self.id}&type=image'
        r = requests.post(url, headers=self.headers, data=image)
        return r
    
    def poll(self):
        '''
        Client asks server to draw a glass brain image
        '''
        if self.fail > 4:
            return
        got = self.lock.acquire(timeout=1)
        self.t = threading.Timer(2, self.poll)
        self.t.daemon = True
        self.t.start()
        if got:
            self.lock.release()
        url = f'http://{self.host}:{self.port}/poll?id={self.id}'
        try:
            r = requests.get(url, headers=self.headers)
            if r.status_code == 200:
                clientReq = r.json()
                if 'regions' in clientReq or 'connections' in clientReq:
                    JsonImage(self, clientReq).post()
                self.fail = 0
            else:
                self.fail += 1
        except:
            self.fail += 1

