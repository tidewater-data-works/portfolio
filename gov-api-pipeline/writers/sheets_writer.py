"""Google Sheets writer. Constructed only when service-account JSON is configured."""

from __future__ import annotations

from typing import Any, Callable

from bls import Observation
from writers.csv_writer import COLUMNS

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsWriter:
    def __init__(
        self,
        spreadsheet_id: str,
        worksheet: str,
        credentials_info: dict[str, Any],
        *,
        credentials_factory: Callable[[dict[str, Any]], Any] | None = None,
        service_builder: Callable[..., Any] | None = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.worksheet = worksheet
        self.credentials_info = credentials_info
        self._credentials_factory = credentials_factory
        self._service_builder = service_builder

    def write(self, rows: list[Observation]) -> None:
        credentials = self._credentials(self.credentials_info)
        builder = self._service_builder or _default_builder
        service = builder("sheets", "v4", credentials=credentials, cache_discovery=False)
        grid = [COLUMNS]
        for row in sorted(rows, key=lambda item: (item.year, item.month)):
            record = row.as_row()
            grid.append([record[column] for column in COLUMNS])
        sheet = "'" + self.worksheet.replace("'", "''") + "'"
        spreadsheets = service.spreadsheets()
        spreadsheets.values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet}!A:E",
        ).execute()
        spreadsheets.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet}!A1",
            valueInputOption="RAW",
            body={"values": grid},
        ).execute()

    def _credentials(self, info: dict[str, Any]) -> Any:
        if self._credentials_factory is not None:
            return self._credentials_factory(info)
        try:
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise RuntimeError("Google Sheets libraries are not installed") from exc
        return Credentials.from_service_account_info(info, scopes=SCOPES)


def _default_builder(api: str, version: str, *, credentials: Any, cache_discovery: bool) -> Any:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Google Sheets libraries are not installed") from exc
    return build(api, version, credentials=credentials, cache_discovery=cache_discovery)
