"""Employee attendance API. Instants cross the API boundary as epoch milliseconds."""
import os, re
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()
client = MongoClient(os.getenv('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=2000, tz_aware=True)
db = client[os.getenv('MONGO_DB', 'attendance_db')]
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
PRESENT = {'PRESENT', 'WFH', 'ON_DUTY'}
app = FastAPI(title='Employee Attendance & Analytics API', version='2.0.0')

@app.on_event('startup')
def indexes():
    db.employees.create_index([('emp_code', ASCENDING)], unique=True, name='uq_emp_code')
    db.employees.create_index([('department', ASCENDING), ('emp_code', ASCENDING)], name='department_emp')
    db.employees.create_index([('department', ASCENDING), ('joined_on', ASCENDING)], name='department_joined')
    db.employees.create_index([('joined_on', ASCENDING)], name='employee_joined')
    db.attendance_logs.create_index([('emp_code', ASCENDING), ('date', ASCENDING)], unique=True, name='uq_attendance_day')
    db.attendance_logs.create_index([('date', DESCENDING), ('emp_code', ASCENDING)], name='attendance_date_emp')
    db.attendance_logs.create_index([('emp_code', ASCENDING), ('date', DESCENDING)], name='attendance_emp_date')

def trunc_dt(v: datetime) -> datetime:
    if v.tzinfo is None: v = v.replace(tzinfo=UTC)
    return v.astimezone(UTC).replace(microsecond=0)
def from_ms(v: int) -> datetime:
    if type(v) is not int or v < 100_000_000_000 or v > 253402300799999:
        raise HTTPException(422, 'timestamp must be epoch milliseconds')
    try: return datetime.fromtimestamp(v / 1000, UTC).replace(microsecond=0)
    except (ValueError, OverflowError, OSError): raise HTTPException(422, 'timestamp out of range')
def to_ms(v):
    if v is None: return None
    if v.tzinfo is None: v = v.replace(tzinfo=UTC)
    return int(v.timestamp() * 1000)
def public(doc):
    if not doc: return doc
    doc = dict(doc); doc.pop('_id', None)
    for k in ('created_at','punch_in','punch_out'):
        if k in doc: doc[k] = to_ms(doc[k])
    doc.setdefault('history', [])
    doc.setdefault('half_day', False)
    doc['history'] = [{**h, 'at':to_ms(h.get('at')), 'changes':{k:{'from':to_ms(v.get('from')) if k in ('punch_in','punch_out') else v.get('from'), 'to':to_ms(v.get('to')) if k in ('punch_in','punch_out') else v.get('to')} for k,v in h.get('changes',{}).items()}} for h in doc['history']]
    return doc
def shift_dt(day: date, value: str) -> datetime:
    h,m=map(int,value.split(':')); return datetime.combine(day,time(h,m),IST).astimezone(UTC)
def attendance_day(punch: datetime, emp) -> date:
    local=punch.astimezone(IST); day=local.date()
    if emp['shift_end'] <= emp['shift_start'] and local.strftime('%H:%M') < emp['shift_end']: day -= timedelta(days=1)
    return day
def late_minutes(punch, emp, day):
    start=shift_dt(day,emp['shift_start']); secs=int((punch-start).total_seconds())
    return secs//60 if secs > 600 else 0
def derived(pin,pout,emp,day):
    late=late_minutes(pin,emp,day) if pin else 0
    if not pin or not pout: return late, None, 0, False
    hours=(Decimal(str(int((pout-pin).total_seconds()))) / Decimal(3600)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    end=shift_dt(day,emp['shift_end'])
    if emp['shift_end'] <= emp['shift_start']: end += timedelta(days=1)
    overtime=int((pout-end).total_seconds())//60
    return late,float(hours),overtime if overtime>=30 else 0,hours<Decimal('4.50')
def valid_date(v):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v or ''): raise HTTPException(422,'date must be YYYY-MM-DD')
    try: return date.fromisoformat(v)
    except (ValueError,TypeError): raise HTTPException(422,'date must be YYYY-MM-DD')
def valid_month(v):
    if not re.fullmatch(r'\d{4}-\d{2}',v or ''): raise HTTPException(422,'month must be YYYY-MM')
    try: return date.fromisoformat(v+'-01')
    except ValueError: raise HTTPException(422,'invalid month')
def page_args(page, page_size):
    if page<1 or page_size<1 or page_size>100: raise HTTPException(422,'invalid pagination')
class StrictModel(BaseModel):
    model_config=ConfigDict(extra='ignore')
class EmployeeIn(StrictModel):
    emp_code:str=Field(pattern=r'^EMP\d{4,6}$'); name:str=Field(min_length=1,max_length=100); email:str=Field(pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$',max_length=120); department:str=Field(min_length=1,max_length=50); shift_start:str='09:30'; shift_end:str='18:30'; joined_on:str
    @field_validator('shift_start','shift_end')
    @classmethod
    def shift(cls,v):
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',v): raise ValueError('time must be HH:MM')
        return v
    @field_validator('joined_on')
    @classmethod
    def joined(cls,v):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v): raise ValueError('date must be YYYY-MM-DD')
        date.fromisoformat(v); return v
    @model_validator(mode='after')
    def shifts_differ(self):
        if self.shift_start==self.shift_end: raise ValueError('shift times must differ')
        return self
class PunchInIn(StrictModel):
    emp_code:str; punched_at:Optional[StrictInt]=None; status:Literal['PRESENT','WFH','ON_DUTY']='PRESENT'
    @field_validator('punched_at')
    @classmethod
    def stamp(cls,v):
        if v is not None: from_ms(v)
        return v
class PunchOutIn(StrictModel):
    emp_code:str; punched_at:Optional[StrictInt]=None
    @field_validator('punched_at')
    @classmethod
    def stamp(cls,v):
        if v is not None: from_ms(v)
        return v
class RegularizeIn(StrictModel):
    status:Literal['PRESENT','ABSENT','LEAVE','WFH','ON_DUTY']=None; punch_in:StrictInt=None; punch_out:StrictInt=None; reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator('punch_in','punch_out')
    @classmethod
    def stamp(cls,v):
        if v is not None: from_ms(v)
        return v

@app.get('/health')
def health():
    try: db.command('ping'); return {'status':'ok'}
    except Exception: raise HTTPException(503,'MongoDB unavailable')

@app.post('/employees',status_code=201)
def create_employee(body:EmployeeIn):
    doc=body.model_dump(); doc['created_at']=datetime.now(UTC)
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'emp_code already exists')
    return public(doc)

@app.get('/employees')
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={} if department is None else {'department':department}; items=list(db.employees.find(q,{'_id':0}).sort('emp_code',ASCENDING).skip((page-1)*page_size).limit(page_size))
    return {'items':[public(d) for d in items],'total':db.employees.count_documents(q),'page':page,'page_size':page_size}

@app.post('/attendance/punch-in',status_code=201)
def punch_in(body:PunchInIn):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'employee not found')
    ts=trunc_dt(from_ms(body.punched_at) if body.punched_at is not None else datetime.now(UTC)); day=attendance_day(ts,emp).isoformat()
    lm=late_minutes(ts,emp,date.fromisoformat(day))
    doc={'emp_code':body.emp_code,'date':day,'status':body.status,'punch_in':ts,'punch_out':None,'work_hours':None,'late_minutes':lm,'overtime_minutes':0,'half_day':False,'history':[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'already punched in for that date')
    return public(doc)

@app.post('/attendance/punch-out')
def punch_out(body:PunchOutIn):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'employee not found')
    ts=trunc_dt(from_ms(body.punched_at) if body.punched_at is not None else datetime.now(UTC))
    rec=db.attendance_logs.find_one({'emp_code':body.emp_code,'punch_in':{'$lte':ts}},sort=[('punch_in',DESCENDING)])
    if not rec: raise HTTPException(404,'no punch-in found')
    if rec.get('punch_out') is not None: raise HTTPException(409,'already punched out')
    pin=rec['punch_in']
    if (ts-pin).total_seconds()<=0 or (ts-pin).total_seconds()>86400: raise HTTPException(422,'punch-out must be within 24 hours')
    lm,wh,ot,half=derived(pin,ts,emp,date.fromisoformat(rec['date']))
    upd=db.attendance_logs.find_one_and_update({'_id':rec['_id'],'punch_out':None},{'$set':{'punch_out':ts,'work_hours':wh,'overtime_minutes':ot,'half_day':half}},return_document=ReturnDocument.AFTER)
    if upd is None: raise HTTPException(409,'already punched out')
    return public(upd)

def attendance_filter(emp_code=None,date_from=None,date_to=None,status=None):
    q={}
    if emp_code is not None:q['emp_code']=emp_code
    if status is not None:q['status']=status
    if date_from or date_to:
        q['date']={}
        if date_from:q['date']['$gte']=date_from
        if date_to:q['date']['$lte']=date_to
    return q
@app.get('/attendance')
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    for d in (date_from,date_to):
        if d:valid_date(d)
    if date_from and date_to and date_from>date_to:raise HTTPException(422,'date_from must not exceed date_to')
    if status and status not in {'PRESENT','ABSENT','LEAVE','WFH','ON_DUTY'}:raise HTTPException(422,'invalid status')
    q=attendance_filter(emp_code,date_from,date_to,status); total=db.attendance_logs.count_documents(q)
    items=list(db.attendance_logs.find(q,{'_id':0}).sort([('date',DESCENDING),('emp_code',ASCENDING)]).skip((page-1)*page_size).limit(page_size))
    return {'items':[public(d) for d in items],'total':total,'page':page,'page_size':page_size}

@app.patch('/attendance/{emp_code}/{day}')
def regularize(emp_code:str,day:str,body:RegularizeIn):
    d=valid_date(day); emp=db.employees.find_one({'emp_code':emp_code}); rec=db.attendance_logs.find_one({'emp_code':emp_code,'date':day})
    if not emp or not rec:raise HTTPException(404,'employee or attendance record not found')
    status=body.status or rec['status']; pin=from_ms(body.punch_in) if body.punch_in is not None else rec.get('punch_in'); pout=from_ms(body.punch_out) if body.punch_out is not None else rec.get('punch_out')
    if status in ('ABSENT','LEAVE'):
        if body.punch_in is not None or body.punch_out is not None:raise HTTPException(422,'cannot set punch times for ABSENT or LEAVE')
        pin=pout=None
    elif not pin:raise HTTPException(422,'presence status requires punch_in')
    if pin:
        pin=trunc_dt(pin)
        if attendance_day(pin,emp)!=d:raise HTTPException(422,'punch_in must be on attendance date')
    if pout:
        pout=trunc_dt(pout)
        if not pin or (pout-pin).total_seconds()<=0 or (pout-pin).total_seconds()>86400:raise HTTPException(422,'invalid punch_out')
    lm,wh,ot,half=derived(pin,pout,emp,d)
    new={'status':status,'punch_in':pin,'punch_out':pout,'late_minutes':lm,'work_hours':wh,'overtime_minutes':ot,'half_day':half}
    old={'status':rec.get('status'),'punch_in':rec.get('punch_in'),'punch_out':rec.get('punch_out'),'late_minutes':rec.get('late_minutes',0),'work_hours':rec.get('work_hours'),'overtime_minutes':rec.get('overtime_minutes',0),'half_day':rec.get('half_day',False)}
    changes={k:{'from':old[k],'to':new[k]} for k in new if old[k]!=new[k]}
    if not changes:raise HTTPException(422,'request makes no changes')
    entry={'at':datetime.now(UTC),'by':body.regularized_by,'reason':body.reason,'changes':changes}
    cas={'_id':rec['_id']}
    if 'history' in rec:cas['history']=rec['history']
    else:cas['$or']=[{'history':{'$exists':False}},{'history':[]}]
    upd=db.attendance_logs.find_one_and_update(cas,{'$set':new,'$push':{'history':entry}},return_document=ReturnDocument.AFTER)
    if upd is None:raise HTTPException(409,'record changed concurrently; retry')
    return public(upd)

def month_bounds(month):
    first=valid_month(month); nextm=date(first.year+first.month//12,first.month%12+1,1)
    return first.isoformat(),nextm.isoformat(),first,nextm-timedelta(days=1)
def round_expr(value,places=2): return {'$round':[value,places]}
def half_up(value,places=2):
    if value is None:return None
    return float(Decimal(str(value)).quantize(Decimal('1').scaleb(-places),rounding=ROUND_HALF_UP))

@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code:str,month:str):
    start,end,first,last=month_bounds(month); emp=db.employees.find_one({'emp_code':emp_code})
    if not emp:raise HTTPException(404,'employee not found')
    joined=max(first,date.fromisoformat(emp['joined_on'])); workdays=sum(1 for i in range((last-joined).days+1) if (joined+timedelta(days=i)).weekday()<5) if joined<=last else 0
    pipe=[{'$match':{'emp_code':emp_code,'date':{'$gte':start,'$lt':end}}},{'$group':{'_id':None,'present':{'$sum':{'$cond':[{'$and':[{'$in':['$status',['PRESENT','WFH','ON_DUTY']]},{'$lt':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},7]},{'$gt':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}},1]}]} ,{'$cond':[{'$eq':[{'$ifNull':['$half_day',False]},True]},0.5,1]},0]}},'leave':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'late':{'$sum':{'$ifNull':['$late_minutes',0]}},'ot':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
    agg=list(db.attendance_logs.aggregate(pipe)); a=agg[0] if agg else {}
    pct=half_up(100*a.get('present',0)/workdays,4) if workdays else None
    return {'emp_code':emp_code,'month':month,'working_days':workdays,'present_days':half_up(a.get('present',0)),'leave_days':a.get('leave',0),'late_count':a.get('late_count',0),'total_late_minutes':a.get('late',0),'total_overtime_minutes':a.get('ot',0),'attendance_pct':pct}

@app.get('/analytics/departments/summary')
def department_summary(month:str,department:Optional[str]=None):
    start,end,first,last=month_bounds(month)
    ematch={'joined_on':{'$lte':last.isoformat()}}
    if department is not None:ematch['department']=department
    pipe=[{'$match':ematch},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',start]},{'$lt':['$date',end]}]}}},{'$project':{'status':1,'work_hours':1,'date':1,'late_minutes':1,'overtime_minutes':1,'half_day':1}}],'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$group':{'_id':'$department','headcount':{'$addToSet':'$emp_code'},'present':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',['PRESENT','WFH','ON_DUTY']]},{'$lt':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$logs.date'}}},7]},{'$gt':[{'$dayOfWeek':{'$dateFromString':{'dateString':'$logs.date'}}},1]}]},{'$cond':[{'$eq':[{'$ifNull':['$logs.half_day',False]},True]},0.5,1]},0]}},'hours':{'$avg':{'$cond':[{'$and':[{'$in':['$logs.status',['PRESENT','WFH','ON_DUTY']]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},'$logs.work_hours',None]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'late':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$project':{'_id':0,'department':'$_id','headcount':{'$size':'$headcount'},'present_days':{'$round':['$present',2]},'avg_work_hours':{'$round':['$hours',2]},'late_count':1,'total_late_minutes':'$late','leave_count':'$leave','on_duty_count':'$on_duty'}},{'$sort':{'department':1}}]
    rows=list(db.employees.aggregate(pipe))
    return {'month':month,'items':[{'department':r['department'],'headcount':r['headcount'],'present_days':half_up(r['present_days']),'avg_work_hours':half_up(r.get('avg_work_hours')),'late_count':r['late_count'],'total_late_minutes':r['total_late_minutes'],'leave_count':r['leave_count'],'on_duty_count':r['on_duty_count']} for r in rows]}

@app.get('/analytics/leaderboard/late')
def leaderboard(month:str,limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    start,end,_,_=month_bounds(month)
    pipe=[{'$match':{'date':{'$gte':start,'$lt':end}}},{'$group':{'_id':'$emp_code','total':{'$sum':{'$ifNull':['$late_minutes',0]}},'count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}},{'$match':{'total':{'$gt':0}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'employee'}},{'$unwind':'$employee'}]
    if department is not None:pipe.append({'$match':{'employee.department':department}})
    pipe += [{'$setWindowFields':{'sortBy':{'total':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$sort':{'total':-1,'_id':1}},{'$project':{'_id':0,'rank':1,'emp_code':'$_id','name':'$employee.name','department':'$employee.department','total_late_minutes':'$total','late_count':'$count'}}]
    return {'month':month,'items':list(db.attendance_logs.aggregate(pipe))}

@app.get('/analytics/departments/{department}/trend')
def department_trend(department:str,from_:str=Query(alias='from'),to:str=Query(alias='to')):
    sd,ed=valid_date(from_),valid_date(to)
    if sd>ed:raise HTTPException(422,'to must not precede from')
    if (ed-sd).days>91:raise HTTPException(422,'range cannot exceed 92 days')
    if not db.employees.find_one({'department':department}):raise HTTPException(404,'department not found')
    # Build a calendar spine in MongoDB, then calculate headcount and daily attendance per date.
    n=(ed-sd).days+1
    pipeline=[
      {'$documents':[{}]},
      {'$set':{'days':{'$map':{'input':{'$range':[0,n]},'as':'i','in':{'$dateAdd':{'startDate':datetime.combine(sd,time.min),'unit':'day','amount':'$$i'}}}}}},
      {'$unwind':'$days'},
      {'$set':{'date_key':{'$dateToString':{'format':'%Y-%m-%d','date':'$days'}},'is_working_day':{'$and':[{'$gte':[{'$dayOfWeek':'$days'},2]},{'$lte':[{'$dayOfWeek':'$days'},6]}]}}},
      {'$lookup':{'from':'employees','let':{'d':'$date_key'},'pipeline':[{'$match':{'department':department,'$expr':{'$lte':['$joined_on','$$d']}}},{'$group':{'_id':None,'headcount':{'$sum':1},'codes':{'$push':'$emp_code'}}}],'as':'staff'}},
      {'$set':{'headcount':{'$ifNull':[{'$arrayElemAt':['$staff.headcount',0]},0]},'codes':{'$ifNull':[{'$arrayElemAt':['$staff.codes',0]},[]]}}},
      {'$lookup':{'from':'attendance_logs','let':{'d':'$date_key','codes':'$codes'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$date','$$d']},{'$in':['$emp_code','$$codes']}]}}},{'$group':{'_id':None,'present_count':{'$sum':{'$cond':[{'$in':['$status',['PRESENT','WFH','ON_DUTY']]},{'$cond':[{'$eq':[{'$ifNull':['$half_day',False]},True]},0.5,1]},0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}}],'as':'totals'}},
      {'$set':{'present_count':{'$ifNull':[{'$arrayElemAt':['$totals.present_count',0]},0]},'late_count':{'$ifNull':[{'$arrayElemAt':['$totals.late_count',0]},0]}}},
      {'$set':{'attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$divide':['$present_count','$headcount']},None]}}},
      {'$setWindowFields':{'sortBy':{'days':1},'output':{'moving_avg_7d':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},
      {'$project':{'_id':0,'date':'$date_key','is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':{'$round':['$attendance_rate',4]},'moving_avg_7d':{'$round':['$moving_avg_7d',4]}}}
    ]
    return {'department':department,'items':list(db.aggregate(pipeline))}
def explain_for(name,params):
    if name=='attendance_list':
        q=attendance_filter(params.get('emp_code'),params.get('date_from'),params.get('date_to'),params.get('status'))
        return db.command('explain',{'find':'attendance_logs','filter':q,'sort':{'date':-1,'emp_code':1},'skip':(int(params.get('page',1))-1)*int(params.get('page_size',20)),'limit':int(params.get('page_size',20))},verbosity='executionStats')
    if name=='employee_monthly':
        start,end,_,_=month_bounds(params['month']); pipe=[{'$match':{'emp_code':params['emp_code'],'date':{'$gte':start,'$lt':end}}},{'$group':{'_id':'$emp_code','count':{'$sum':1}}}]
        return db.command('explain',{'aggregate':'attendance_logs','pipeline':pipe,'cursor':{}},verbosity='executionStats')
    if name=='department_summary':
        start,end,_,last=month_bounds(params['month']); pipe=[{'$match':{'joined_on':{'$lte':last.isoformat()}}},{'$lookup':{'from':'attendance_logs','localField':'emp_code','foreignField':'emp_code','as':'logs'}}]
        return db.command('explain',{'aggregate':'employees','pipeline':pipe,'cursor':{}},verbosity='executionStats')
    if name=='late_leaderboard':
        start,end,_,_=month_bounds(params['month']); pipe=[{'$match':{'date':{'$gte':start,'$lt':end}}},{'$group':{'_id':'$emp_code','total':{'$sum':'$late_minutes'}}}]
        return db.command('explain',{'aggregate':'attendance_logs','pipeline':pipe,'cursor':{}},verbosity='executionStats')
    if name=='department_trend':
        sd,ed=valid_date(params['from_']),valid_date(params['to'])
        if sd>ed or (ed-sd).days>91: raise HTTPException(422,'invalid trend range')
        n=(ed-sd).days+1
        pipe=[{'$documents':[{}]},{'$set':{'days':{'$map':{'input':{'$range':[0,n]},'as':'i','in':{'$dateAdd':{'startDate':datetime.combine(sd,time.min),'unit':'day','amount':'$$i'}}}}}},{'$unwind':'$days'},{'$set':{'date_key':{'$dateToString':{'format':'%Y-%m-%d','date':'$days'}}}},{'$lookup':{'from':'employees','let':{'d':'$date_key'},'pipeline':[{'$match':{'department':params['department'],'$expr':{'$lte':['$joined_on','$$d']}}},{'$group':{'_id':None,'headcount':{'$sum':1},'codes':{'$push':'$emp_code'}}}],'as':'staff'}},{'$set':{'codes':{'$ifNull':[{'$arrayElemAt':['$staff.codes',0]},[]]}}},{'$lookup':{'from':'attendance_logs','let':{'d':'$date_key','codes':'$codes'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$date','$$d']},{'$in':['$emp_code','$$codes']}]}}},{'$group':{'_id':None,'count':{'$sum':1}}}],'as':'totals'}}]
        return db.command('explain',{'aggregate':1,'pipeline':pipe,'cursor':{}},verbosity='executionStats')
    raise HTTPException(422,'unknown endpoint')
@app.get('/admin/explain/{endpoint}')
def admin_explain(endpoint:str,emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),month:Optional[str]=None,limit:int=Query(10,ge=1,le=50),department:Optional[str]=None,from_:Optional[str]=Query(default=None,alias='from'),to:Optional[str]=None):
    p=locals()
    try:
        result=explain_for(endpoint,p)
        collection='employees' if endpoint in ('department_summary','department_trend') else 'attendance_logs'
        return {'endpoint':endpoint,'collection':collection,'explain':result}
    except KeyError as e: raise HTTPException(422,f'missing query parameter {e.args[0]}')
