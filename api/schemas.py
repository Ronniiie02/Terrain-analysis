# -*- coding: utf-8 -*-
"""
API request/response schemas
"""
from typing import Optional, Literal
from pydantic import BaseModel, Field

class CreateRunRequest(BaseModel):
    """
    Request to create a new terrain analysis run.
    - address
    - lat+lon
    - address + lat/lon
    """
    address: Optional[str] = Field(default=None, description="Free-form address text")
    lat: Optional[float] = Field(default=None, description="Latitude (WGS84)")
    lon: Optional[float] = Field(default=None, description="Longitude (WGS84)")

    table_format: Literal["parquet", "csv", "feather", "both"] = Field(
        default="csv",
        description="Output table format"
    )
    verbose: bool = Field(default=True, description="Print progress messages")
    generate_narrative: bool = Field(
        default=False,
        description="Generate narrative summary files"
    )

class RunStatus(BaseModel):
    """Status of a terrain analysis run"""
    run_id: str = Field(..., description="Unique run identifier")
    status: Literal["queued", "running", "done", "error"] = Field(
        ...,
        description="Current status of the run"
    )
    message: Optional[str] = Field(None, description="Status message or error details")
    outputs_dir: Optional[str] = Field(None, description="Path to output directory")

__all__ = ["CreateRunRequest", "RunStatus"]
