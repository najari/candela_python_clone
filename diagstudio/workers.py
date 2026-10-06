import logging
import threading
import uuid
from PySide6.QtCore import QObject, Signal, Slot

from .application import Application
from .domain import DomainError
from .interchange import cdd_preview, csv_read, csv_map, export_csv, export_odx
from .persistence import Session


class Worker(QObject):
    finished=Signal(str,object,object)
    progress=Signal(str,str)

    def __init__(self):
        super().__init__()
        self.app=Application()
        self.cancel_event=threading.Event()
        self.staged={}

    def cancel(self):
        self.cancel_event.set()

    def checkpoint(self):
        if self.cancel_event.is_set(): raise DomainError("작업이 취소되었습니다.","CANCELLED")

    @Slot(str,str,object)
    def run(self,job,method,args):
        self.cancel_event.clear()
        self.progress.emit(job,"처리 중…")
        try:
            a=self.app
            if method=="recoveries": result=Session.recoveries()
            elif method=="quarantine_recovery": result=Session.quarantine_recovery(args["session_id"])
            elif method=="cdd_preview":
                preview=cdd_preview(args["path"],self.checkpoint)
                token=str(uuid.uuid4())
                self.staged.clear()
                self.staged[token]=preview
                result={"preview_id":token,"basename":preview["basename"],"document_type":preview.get("document_type","CDD"),"version":preview.get("version"),"coverage":preview["coverage"],"issues":[{"path":n["node_path"],"reason":n["reason_code"],"semantic":n["semantic"]} for n in preview["unsupported"]]}
            elif method=="apply_cdd":
                result=a.apply_cdd(self.staged.pop(args["preview_id"]),self.checkpoint)
            elif method=="csv_read": result=csv_read(**args)
            elif method=="csv_map":
                existing=[r["code"] for r in a.r.all("SELECT code FROM variant_dtcs WHERE variant_id=?",(args.pop("variant_id"),))]
                result=csv_map(**args,existing=existing)
            elif method=="export_csv": result=export_csv(a.r.snapshot(a.locale),**args)
            elif method=="export_odx": result=export_odx(a.r.snapshot(a.locale,include_sources=True),**args,cancel=self.checkpoint)
            elif method=="save_project": result=a.save_project(**args,cancel=self.checkpoint)
            elif method=="validate_project": result=a.validate_project(self.checkpoint,**args)
            elif method=="compare_project_file": result=a.compare_project_file(**args,cancel=self.checkpoint)
            elif method=="compare_view": result=a.compare_view(**args,cancel=self.checkpoint)
            elif method=="inspect":
                result={"details":a.details(args["oid"]),"catalog":a.catalog()}
                if result["details"]["kind"]=="data_type":result["impact"]=a.reference_impact(args["oid"])
            elif method=="shutdown":
                a.close_project()
                result=None
            else:
                allowed={"new_project","open_project","close_project","overview","query_objects","execute_command","catalog","details","reference_impact","deletion_impact","preview_fields","build_telegram_preview","set_locale","locate_object","object_path","node_path","resolve_path","node_name_path","object_name_path","resolve_name_path","find_document","qualifier_overview","related_targets","attribute_overview","compact_view","compare_variants","compare_file_variants","state_graph","translation_overview",
                         "compact_tree","shortcut_matrix","revision_history","fault_memory_view","rework_items","property_sheet","object_qualifier_path","selection_details","member_counterpart",
                         "replace_type_preview","variant_wizard_items","suggest_number","add_defaults"}
                if method not in allowed: raise DomainError("알 수 없는 작업입니다.")
                result=getattr(a,method)(**args)
            self.finished.emit(job,result,None)
        except DomainError as e:
            self.finished.emit(job,None,{"code":e.code,"message":str(e)})
        except Exception as e:
            # 진단 원문 또는 예외 인수는 기본 로그에 남기지 않는다.
            logging.error("job=%s operation=%s exception_type=%s",job,method,type(e).__name__)
            import sqlite3
            message="중복 값 또는 사용 중인 참조가 있습니다." if isinstance(e,sqlite3.IntegrityError) else f"{type(e).__name__}: 작업을 완료할 수 없습니다. 작업 ID {job}"
            if isinstance(e,(OSError,ValueError)): message=str(e)
            self.finished.emit(job,None,{"code":"INTERNAL_ERROR","message":message})
