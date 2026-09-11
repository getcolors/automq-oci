import boto3,os,json
from botocore.config import Config
from botocore.exceptions import ClientError
s=boto3.client('s3',endpoint_url='https://frqagv4ppzaz.compat.objectstorage.eu-frankfurt-1.oraclecloud.com',region_name='eu-frankfurt-1',aws_access_key_id=os.environ['COLORS_PAR_OCI_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['COLORS_PAR_OCI_SECRET_ACCESS_KEY'],config=Config(s3={'addressing_style':'path'}))
b='automq-oci-compat-probe-frqagv4ppzaz'; k='isolated-preconditions'; result={}
def put(value,condition=None):
 def header(request,**kwargs):
  for name,val in (condition or {}).items(): request.headers[name]=val
 s.meta.events.register('before-sign.s3.PutObject',header)
 try:
  r=s.put_object(Bucket=b,Key=k,Body=value.encode());return r['ResponseMetadata']['HTTPStatusCode']
 except ClientError as e: return e.response['ResponseMetadata']['HTTPStatusCode']
 finally:s.meta.events.unregister('before-sign.s3.PutObject',header)
try:
 result['initial_create']=put('first',{'If-None-Match':'*'})
 etag=s.head_object(Bucket=b,Key=k)['ETag']
 result['competing_create']=put('competitor',{'If-None-Match':'*'})
 result['exact_etag_replace']=put('second',{'If-Match':etag})
 result['stale_etag_replace']=put('stale',{'If-Match':etag})
 result['final_value']=s.get_object(Bucket=b,Key=k)['Body'].read().decode()
finally:s.delete_object(Bucket=b,Key=k)
print(json.dumps(result,indent=2))
