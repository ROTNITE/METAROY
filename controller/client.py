"""Public HTTP client. No environment/layout/truth module is imported here."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import time
from urllib.parse import urlencode,urlsplit
import uuid

from contracts.protocol import (VERSION, MAX_RESPONSE_BYTES, TIME_EPSILON, ProtocolError,
                                encode, strict_json, validate_episode_response,
                                validate_observation)
from controller.public_core import RESET_DEDUPLICATION, STEP_DEDUPLICATION, PublicCore


@dataclass(frozen=True)
class PreparedRequest:
    method: str
    path: str
    body: bytes | None = None


class TransportFailure(RuntimeError):
    """No valid response; the caller must not assume a state-changing rollback."""
    def __init__(self,message,*,state_may_have_changed):
        super().__init__(message)
        self.state_may_have_changed=state_may_have_changed


class EnvironmentClient:
    def __init__(self,url='http://127.0.0.1:8080',*,timeout_s=30,audit=None,core_mode='declared',compatibility_audit=None):
        target=urlsplit(url)
        if target.scheme not in ('http','https') or not target.hostname or target.username or target.password:
            raise ValueError('Environment URL must be HTTP(S) without embedded credentials')
        if target.path not in ('','/') or target.query or target.fragment:
            raise ValueError('Supply the environment origin, without an endpoint path')
        self.target=target;self.timeout_s=timeout_s;self.audit=audit
        self.connection=None
        self.core_mode,self.compatibility_audit=core_mode,compatibility_audit
        self.extensions=set()
        self.public=None

    def disconnect(self):
        if self.connection:self.connection.close()
        self.connection=None

    def _connection(self):
        if self.connection is None:
            constructor=http.client.HTTPSConnection if self.target.scheme=='https' else http.client.HTTPConnection
            self.connection=constructor(self.target.hostname,self.target.port,timeout=self.timeout_s)
        return self.connection

    @staticmethod
    def prepare_step(decision):
        # Serialize exactly once, before any retry or ambiguous network effect.
        return PreparedRequest('POST','/v1/environment/step',encode(decision))

    def submit(self,request: PreparedRequest,*,network_retries=0):
        methods={'GET':{'health','capabilities','observation','logs'},'POST':{'reset','step','close'}}
        if (not isinstance(request,PreparedRequest) or request.method not in methods or
            urlsplit(request.path).path not in {'/v1/environment/'+name for name in methods[request.method]}):
            raise ValueError('Only prepared public environment requests are supported')
        if type(network_retries) is not int or not 0<=network_retries<=3:
            raise ValueError('network_retries must be 0..3')
        for attempt in range(network_retries+1):
            started=time.perf_counter();raw=None;status=None
            try:
                headers={'Accept':'application/json'}
                if request.body is not None:headers['Content-Type']='application/json; charset=utf-8'
                connection=self._connection()
                connection.request(request.method,request.path,body=request.body,headers=headers)
                response=connection.getresponse();status=response.status
                raw=response.read(MAX_RESPONSE_BYTES+1)
            except (OSError,http.client.HTTPException) as error:
                self.disconnect()
                if self.audit:self.audit({'kind':'http_transport_failure','method':request.method,'path':request.path,
                    'body':request.body,'attempt':attempt,'elapsed_s':time.perf_counter()-started,
                    'error_type':type(error).__name__,'state_may_have_changed':request.method!='GET'})
                if attempt<network_retries:continue
                raise TransportFailure(type(error).__name__,state_may_have_changed=request.method!='GET') from error
            if self.audit:self.audit({'kind':'http_exchange','method':request.method,'path':request.path,
                'body':request.body,'response':raw,'status':status,'attempt':attempt,'elapsed_s':time.perf_counter()-started})
            try:
                result=strict_json(raw,maximum_bytes=MAX_RESPONSE_BYTES)
                if not isinstance(result,dict):raise ProtocolError('INVALID_REQUEST','Response must be an object')
                if result.get('protocol_version')!=VERSION:
                    raise ProtocolError('UNSUPPORTED_PROTOCOL_VERSION','Environment response version is unsupported')
                if 'error' in result:
                    failure=result['error']
                    if (not isinstance(failure,dict) or not isinstance(failure.get('code'),str)
                            or (failure.get('details') is not None and not isinstance(failure['details'],dict))):
                        raise ProtocolError('INVALID_REQUEST','Malformed environment error')
                    if request.method=='POST' and request.body:
                        sent=strict_json(request.body)
                        if isinstance(sent,dict):
                            for key in ('request_id','episode_id'):
                                if (sent.get(key) is not None and result.get(key) is not None
                                        and result[key]!=sent[key]):
                                    raise ProtocolError('INVALID_REQUEST','Error belongs to another request or episode')
                if 'error' not in result and status>=400:
                    raise ProtocolError('INTERNAL_ERROR','HTTP failure without normative error',{'status':status})
            except ProtocolError as error:
                self.disconnect()
                raise TransportFailure('Invalid environment response: '+str(error),state_may_have_changed=request.method!='GET') from error
            if 'error' in result:
                error=result['error']
                # A foreign Core error may omit episode_id. Distinguish an
                # explicit rejection from a malformed/ambiguous wire response.
                raise ProtocolError(error['code'],str(error.get('message','Environment rejected request')),error.get('details') or {})
            return result

    def _episode(self,request,*,step=False):
        declares = STEP_DEDUPLICATION if step else RESET_DEDUPLICATION
        result=self.submit(request,network_retries=int(declares in self.extensions))
        return self.episode_response(result,step=step,request=request)

    def episode_response(self,result,*,step=False,request=None):
        """Check the wire envelope and the exact operation that caused it.

        A self-consistent response for another request is not an explicit
        rejection and cannot advance controller memory or custody.
        """
        try:
            if self.public and isinstance(result,dict) and 'observation' in result:
                result={**result,'observation':self.public.wire_observation(result['observation'])}
            response=validate_episode_response(result,step=step,allow_space=bool(self.public and self.public.allow_space))
            if request is not None:
                sent=strict_json(request.body)
                if response['request_id']!=sent.get('request_id'):
                    raise ProtocolError('INVALID_REQUEST','Response belongs to another request')
                if step:
                    if response['episode_id']!=sent.get('episode_id'):
                        raise ProtocolError('UNKNOWN_EPISODE','Response belongs to another episode')
                    # A terminal transition may occur immediately. A running
                    # step must advance time; no response may rewind it.
                    if (response['time']+TIME_EPSILON<sent['time'] or
                            response['status']=='running' and response['time']<=sent['time']):
                        raise ProtocolError('INVALID_REQUEST','Step response does not advance model time')
                elif abs(response['time'])>TIME_EPSILON:
                    raise ProtocolError('INVALID_REQUEST','Reset must start at model time zero')
            return response
        except ProtocolError as error:
            self.disconnect()
            location=(error.details or {}).get('path')
            suffix=' at /'+'/'.join(map(str,location)) if location else ''
            raise TransportFailure('Inconsistent episode response: '+str(error)+suffix,state_may_have_changed=True) from error

    def _get(self,name,**parameters):
        suffix='?'+urlencode(parameters) if parameters else ''
        return self.submit(PreparedRequest('GET','/v1/environment/'+name+suffix),network_retries=0)

    def health(self):return self._get('health')
    def capabilities(self):
        result=self._get('capabilities')
        declared=result.get('extensions')
        self.extensions={name for name in declared if isinstance(name,str)} if isinstance(declared,list) else set()
        self.public=PublicCore(result,mode=self.core_mode,audit=self.compatibility_audit)
        return result

    def observation(self,request_id=None):
        wire=self._get('observation',request_id=request_id or uuid.uuid4().hex)
        view=self.public.wire_observation(wire) if self.public else wire
        return validate_observation(view,allow_space=bool(self.public and self.public.allow_space))

    def reset(self,*,seed,mission=None,request_id=None):
        body={'protocol_version':VERSION,'request_id':request_id or uuid.uuid4().hex,
              'seed':seed,'mission':mission or {'id':'search_and_rescue','parameters':{}}}
        return self._episode(PreparedRequest('POST','/v1/environment/reset',encode(body)))

    def step(self,decision):
        return self._episode(self.prepare_step(decision),step=True)

    def logs(self,episode_id,*,cursor=None):
        params={'episode_id':episode_id}
        if cursor is not None:params['cursor']=cursor
        return self._get('logs',**params)

    def close(self,episode_id,*,request_id=None):
        body=encode({'protocol_version':VERSION,'episode_id':episode_id,'request_id':request_id or uuid.uuid4().hex})
        return self.submit(PreparedRequest('POST','/v1/environment/close',body))
