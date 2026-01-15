from django.db import transaction
from django.http import HttpResponse
from rest_framework import viewsets, permissions, authentication, filters
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.pagination import PageNumberPagination
import csv, io, re, os
from rest_framework.response import Response
from rest_framework import status
from rest_framework import serializers as drf_serializers

from ..campaigns.models import CallLog, Campaign
from ..lists.models import LeadList

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import xlrd
except ImportError:
    xlrd = None
from .models import Lead
from .serializers import LeadSerializer, LeadAdminImportSerializer, normalize_to_e164
from django.db.models import Q, OuterRef, Subquery
def _sentiment_from_score(score):
    if score is None:
        return None
    try:
        s = int(score)
    except Exception:
        return None
    if s > 7:
        return "positive"
    if s < 4:
        return "negative"
    return "neutral"

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    pagination_class = StandardResultsSetPagination

    @action(detail=True, methods=["get"], url_path="view")
    def view(self, request, pk=None):

        lead = get_object_or_404(Lead, pk=pk, owner=request.user)

        lists_qs = (
            lead.lists.all()
            .order_by("name")
            .values("id", "name", "created_at")
        )
        lists_payload = [
            {"id": x["id"], "name": x["name"], "date_added": x["created_at"]}
            for x in lists_qs
        ]

        eligible_calls = (
            CallLog.objects.filter(
                owner=request.user,
                lead_id=lead.id,
                status=CallLog.Status.COMPLETED,
            )
            .order_by("-created_at")
        )
        latest_call_for_campaign = eligible_calls.filter(campaign_id=OuterRef("pk"))

        campaigns_qs = (
            Campaign.objects.filter(owner=request.user, campaign_leads__lead_id=lead.id)
            .distinct()
            .annotate(
                last_call_id=Subquery(latest_call_for_campaign.values("id")[:1]),
                last_call_score=Subquery(latest_call_for_campaign.values("score")[:1]),
                last_call_started_at=Subquery(latest_call_for_campaign.values("started_at")[:1]),
            )
            .order_by("-started_at", "-created_at")
        )

        call_ids = list(campaigns_qs.values_list("last_call_id", flat=True))
        calls_by_id = {
            c.id: c
            for c in CallLog.objects.filter(id__in=call_ids).select_related("campaign")
        }

        campaign_history = []
        dropdown_campaigns = []
        call_media = []

        for camp in campaigns_qs:
            call = calls_by_id.get(camp.last_call_id)
            score = getattr(camp, "last_call_score", None)
            sentiment = _sentiment_from_score(score)

            campaign_history.append({
                "id": camp.id,
                "name": camp.name,
                "start_date": camp.started_at,
                "score": score,
                "status": camp.status,
                "sentiment": sentiment,
            })

            dropdown_campaigns.append({"id": camp.id, "name": camp.name})

            if call:
                backend_file_url = None
                if call.audio_file:
                    try:
                        backend_file_url = request.build_absolute_uri(call.audio_file.url)
                    except Exception:
                        backend_file_url = None

                primary_url = call.recording_url or backend_file_url

                transcript_preview = (call.transcript_text or "").strip()
                if len(transcript_preview) > 400:
                    transcript_preview = transcript_preview[:400].rstrip() + "…"

                call_media.append({
                    "call_id": call.id,
                    "campaign_id": call.campaign_id,
                    "campaign_name": call.campaign.name if call.campaign_id else "",
                    "started_at": call.started_at,
                    "score": call.score,
                    "sentiment": _sentiment_from_score(call.score),
                    "recording_url": primary_url,
                    "audio_file_url": backend_file_url,
                    "transcript_preview": transcript_preview,
                    "transcript_full": call.transcript_text or "",
                })

        payload = {
            "lead": LeadSerializer(lead).data,
            "lists": lists_payload,
            "campaign_history": campaign_history,
            "call_media": call_media,
            "dropdown_campaigns": dropdown_campaigns,
        }
        return Response(payload)

    @action(detail=False, methods=["get"], url_path="import-sample")
    def import_sample(self, request):
        """
        Returns a sample CSV with required headers.
        """
        headers = [
            "Name", "Position", "Email", "Phone number",
            "Company", "Industry", "Country", "Address", "Language"
        ]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        csv_bytes = buf.getvalue().encode("utf-8")
        resp = HttpResponse(csv_bytes, content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="leads_sample.csv"'
        return resp

    @action(detail=False, methods=["post"], url_path="import")
    def import_leads(self, request):
        """
        Multipart upload: file + create_list(bool) + list_name(optional)
        Validates required headers & each row via LeadSerializer.
        Optionally creates a LeadList and attaches created leads.
        """
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        create_list = str(request.data.get("create_list", "")).lower() in {"1", "true", "yes", "on"}
        list_name = (request.data.get("list_name") or "").strip()

        if create_list and not list_name:
            return Response({"detail": "List name is required when creating a list."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Parse rows
        try:
            rows = self._parse_upload_to_rows(upload)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if not rows:
            return Response({"detail": "The file contains no data rows."}, status=status.HTTP_400_BAD_REQUEST)

        created_objs: list[Lead] = []
        errors: list[dict] = []
        normalized_numbers: list[str] = []

        # File-level duplicate detection (after normalization)
        file_seen: dict[str, int] = {}  # phone -> first_row_index
        for idx, row in enumerate(rows, start=2):
            try:
                row['phone_number'] = normalize_to_e164(row.get('phone_number') or '')
            except drf_serializers.ValidationError as ve:
                msg = str(ve.detail[0] if isinstance(ve.detail, list) else ve.detail)
                errors.append({"row": idx, "errors": {"phone_number": [msg]}})
                row['__skip__'] = True
                continue

            p = row['phone_number']
            if p in file_seen:
                errors.append({"row": idx,
                               "errors": {"phone_number": [f"Duplicate phone in file (also at row {file_seen[p]})."]}})
                row['__skip__'] = True
            else:
                file_seen[p] = idx

        # Validate rows (for everything else) and stage for bulk insert
        with transaction.atomic():
            for idx, row in enumerate(rows, start=2):
                if row.get('__skip__'):
                    continue
                ser = LeadSerializer(data=row, context={"request": request})
                if ser.is_valid():
                    vd = ser.validated_data
                    created_objs.append(Lead(**vd))
                    normalized_numbers.append(vd['phone_number'])
                else:
                    errors.append({"row": idx, "errors": ser.errors})

            created = 0
            if created_objs:
                Lead.objects.bulk_create(created_objs, batch_size=500)
                created = len(created_objs)

            list_info = None
            if create_list and created > 0:
                lead_list, created_flag = LeadList.objects.get_or_create(
                    owner=request.user,
                    name=list_name,
                    defaults={"country": ""},
                )
                ids = list(
                    Lead.objects
                    .filter(owner=request.user, phone_number__in=normalized_numbers)
                    .values_list('id', flat=True)
                )
                if ids:
                    lead_list.leads.add(*ids)
                list_info = {"id": lead_list.id, "name": lead_list.name, "created": created_flag}

        return Response({
            "created": created,
            "failed": len(errors),
            "errors": errors,
            "list": list_info
        }, status=status.HTTP_200_OK)

    # ------- helpers --------
    def _parse_upload_to_rows(self, upload_file):
        """
        Returns a list of dicts with canonical field names expected by LeadSerializer.
        Validates required headers are present.
        """
        name = (upload_file.name or "").lower()
        if name.endswith(".csv"):
            return self._parse_csv(upload_file)
        elif name.endswith(".xlsx"):
            if not openpyxl:
                raise ValueError("Excel (.xlsx) support requires 'openpyxl' to be installed.")
            return self._parse_xlsx(upload_file)
        elif name.endswith(".xls"):
            if not xlrd:
                raise ValueError("Legacy Excel (.xls) support requires 'xlrd' to be installed.")
            return self._parse_xls(upload_file)
        else:
            raise ValueError("Unsupported file type. Please upload .csv, .xlsx, or .xls.")

    @staticmethod
    def _normalize_header(h: str) -> str:
        return re.sub(r"\s+", " ", h.strip().lower().replace("_", " "))

    def _header_map(self, headers):
        """
        Map incoming headers to canonical serializer field names.
        """
        alias = {
            "name": "name",
            "position": "position",
            "email": "email",
            "phone": "phone number",
            "phone number": "phone number",
            "phone_number": "phone number",
            "company": "company",
            "industry": "industry",
            "country": "country",
            "address": "address",
            "language": "language",
        }
        canonical = {
            "name": "name",
            "position": "position",
            "email": "email",
            "phone number": "phone_number",
            "company": "company",
            "industry": "industry",
            "country": "country",
            "address": "address",
            "language": "language",
        }
        normalized = [self._normalize_header(h) for h in headers]
        mapped = {}
        for i, nh in enumerate(normalized):
            key = alias.get(nh)
            if key and key in canonical:
                mapped[canonical[key]] = i

        required = ["name", "position", "email", "phone_number", "company", "industry", "country", "address",
                    "language"]
        missing = [r for r in required if r not in mapped]
        if missing:
            # Report the human-friendly headers that are required
            readable = {
                "name": "Name",
                "position": "Position",
                "email": "Email",
                "phone_number": "Phone number",
                "company": "Company",
                "industry": "Industry",
                "country": "Country",
                "address": "Address",
                "language": "Language",
            }
            missing_names = [readable[m] for m in missing]
            raise ValueError(f"Missing required header(s): {', '.join(missing_names)}.")
        return mapped

    def _parse_csv(self, upload_file):
        data = upload_file.read()
        # Try sniffing dialect, handle UTF-8 with BOM
        sample = data[:2048]
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:1024])
        except Exception:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        rows = list(reader)
        if not rows:
            return []
        header_map = self._header_map(rows[0])
        result = []
        for r in rows[1:]:
            if not any((c or "").strip() for c in r):
                continue  # skip empty lines
            d = {}
            for field, idx in header_map.items():
                val = (r[idx] if idx < len(r) else "").strip()
                d[field] = val
            result.append(d)
        return result

    def _parse_xlsx(self, upload_file):
        wb = openpyxl.load_workbook(upload_file, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [(c or "").strip() if isinstance(c, str) else (str(c) if c is not None else "") for c in rows[0]]
        header_map = self._header_map(headers)
        result = []
        for row in rows[1:]:
            if not any(row):
                continue
            d = {}
            for field, idx in header_map.items():
                cell = row[idx] if idx < len(row) else ""
                val = cell if isinstance(cell, str) else (str(cell) if cell is not None else "")
                d[field] = val.strip()
            result.append(d)
        return result

    def _parse_xls(self, upload_file):
        # xlrd needs a bytes buffer
        content = upload_file.read()
        book = xlrd.open_workbook(file_contents=content)
        sheet = book.sheet_by_index(0)
        headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
        header_map = self._header_map(headers)
        result = []
        for r in range(1, sheet.nrows):
            row_vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
            if not any(row_vals):
                continue
            d = {}
            for field, idx in header_map.items():
                cell = row_vals[idx] if idx < len(row_vals) else ""
                val = cell if isinstance(cell, str) else (str(cell) if cell is not None else "")
                d[field] = val.strip()
            result.append(d)
        return result
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "name", "position", "phone_number", "industry",
        "country", "language", "email", "company", "address"
    ]
    ordering_fields = [
        "name", "position", "phone_number", "industry",
        "country", "language", "created_at"
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Lead.objects.filter(owner=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_staff)
class AdminLeadViewSet(viewsets.ModelViewSet):
    """
    Admin-only endpoints that operate on UNOWNED leads:
    - GET /admin/leads/            list/search/sort/paginate unowned leads
    - POST /admin/leads/import     bulk import as unowned (no list creation)
    - GET  /admin/leads/import-sample  download CSV sample
    """
    serializer_class = LeadSerializer  # for GET/serialize
    permission_classes = [IsAdmin]
    authentication_classes = [
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
        JWTAuthentication,
    ]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "name", "position", "phone_number", "industry",
        "country", "language", "email", "company", "address"
    ]
    ordering_fields = [
        "name", "position", "phone_number", "industry",
        "country", "language", "created_at"
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        # Only unowned leads
        return Lead.objects.order_by("-created_at")

    def perform_create(self, serializer):
        # If admins create single records via POST, force unowned
        serializer.save(owner=None)

    # --- Import sample (same as user viewset, different route) ---
    @action(detail=False, methods=["get"], url_path="import-sample")
    def admin_import_sample(self, request):
        headers = [
            "Name", "Position", "Email", "Phone number",
            "Company", "Industry", "Country", "Address", "Language"
        ]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        csv_bytes = buf.getvalue().encode("utf-8")
        resp = HttpResponse(csv_bytes, content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="leads_sample.csv"'
        return resp

    # --- Bulk import (no list creation here) ---
    @action(detail=False, methods=["post"], url_path="import")
    def admin_import_leads(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows = self._parse_upload_to_rows(upload)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({"detail": "The file contains no data rows."}, status=status.HTTP_400_BAD_REQUEST)

        created_objs: list[Lead] = []
        errors: list[dict] = []

        # Normalize and detect file-level duplicates
        file_seen: dict[str, int] = {}
        for idx, row in enumerate(rows, start=2):
            try:
                row['phone_number'] = normalize_to_e164(row.get('phone_number') or '')
            except drf_serializers.ValidationError as ve:
                msg = str(ve.detail[0] if isinstance(ve.detail, list) else ve.detail)
                errors.append({"row": idx, "errors": {"phone_number": [msg]}})
                row['__skip__'] = True
                continue

            p = row['phone_number']
            if p in file_seen:
                errors.append({"row": idx,
                               "errors": {"phone_number": [f"Duplicate phone in file (also at row {file_seen[p]})."]}})
                row['__skip__'] = True
            else:
                file_seen[p] = idx

        normalized_batch = [r['phone_number'] for r in rows if not r.get('__skip__')]

        # DB-level dedupe among UNOWNED leads
        existing_phones = set(
            Lead.objects
            .filter(owner__isnull=True, phone_number__in=normalized_batch)
            .values_list("phone_number", flat=True)
        )

        with transaction.atomic():
            for idx, row in enumerate(rows, start=2):
                if row.get('__skip__'):
                    continue

                if row['phone_number'] in existing_phones:
                    errors.append({"row": idx, "errors": {
                        "phone_number": ["A lead with this phone already exists among unassigned leads."]}})
                    continue

                ser = LeadAdminImportSerializer(data=row, context={"request": request})
                if not ser.is_valid():
                    errors.append({"row": idx, "errors": ser.errors})
                    continue

                vd = ser.validated_data  # already normalized by serializer
                created_objs.append(Lead(**vd))

            created = 0
            if created_objs:
                Lead.objects.bulk_create(created_objs, batch_size=500)
                created = len(created_objs)

        return Response(
            {"created": created, "failed": len(errors), "errors": errors},
            status=status.HTTP_200_OK
        )

    _parse_upload_to_rows = LeadViewSet._parse_upload_to_rows
    _parse_csv = LeadViewSet._parse_csv
    _parse_xlsx = LeadViewSet._parse_xlsx
    _parse_xls = LeadViewSet._parse_xls
    _normalize_header = staticmethod(LeadViewSet._normalize_header)
    _header_map = LeadViewSet._header_map