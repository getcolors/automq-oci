import subprocess,json
url='https://objectstorage.eu-frankfurt-1.oraclecloud.com/n/frqagv4ppzaz/b/automq-oci-native-probe-frqagv4ppzaz/o/isolated-native-preconditions'
def call(method,body=None,headers=None):
 args=['oci','raw-request','--auth','security_token','--http-method',method,'--target-uri',url]
 if body is not None:args+=['--request-body',json.dumps(body)]
 if headers:args+=['--request-headers',json.dumps(headers)]
 r=subprocess.run(args,capture_output=True,text=True)
 if r.returncode:raise RuntimeError('native request CLI failed')
 d=json.loads(r.stdout);return d
call('DELETE')
a=call('PUT',{'value':'first'},{'if-none-match':'*'});b=call('PUT',{'value':'competitor'},{'if-none-match':'*'});c=call('PUT',{'value':'second'},{'if-match':a['headers']['etag']});d=call('PUT',{'value':'stale'},{'if-match':a['headers']['etag']});last=call('GET')
result={'initial_create':a['status'],'competing_create':b['status'],'exact_etag_replace':c['status'],'stale_etag_replace':d['status'],'etag_changed':a['headers']['etag']!=c['headers']['etag'],'final_value':last['data']}
call('DELETE');print(json.dumps(result,indent=2))
