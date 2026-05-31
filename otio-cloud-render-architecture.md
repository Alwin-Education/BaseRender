**OTIO Cloud Render Pipeline**

_Architecture Proposal — Draft_

May 2026

> **Status:** This document describes the target product architecture. The repo already implements the OTIO renderer, S3 media linking, LUT assignment, render job API, polling worker service, and web UI. NLE-to-OTIO conversion, CDL extraction/application, and alternative job backends are planned but not implemented yet.

# **Overview**

An open source, self-hostable platform that accepts NLE project files (Premiere Pro XML, FCPXML, AAF), converts them to OpenTimelineIO, links to user-owned cloud media, and renders a finished video via FFmpeg — deployable by anyone with a Render.com account and an S3-compatible bucket.

# **Problem**

*   No open source tool connects NLE XML/OTIO interchange to cloud rendering in a user-friendly way.
*   Existing cloud render services (OpenShot Cloud, MediaConvert) require either vendor lock-in or uploading all source footage.
*   Users with footage already in cloud storage have no way to reference it directly in a render pipeline.

# **Proposed Solution**

A lightweight web application that:

*   Accepts Premiere Pro XML, FCPXML, or AAF uploads and converts them to OTIO using existing open source adapters.
*   Lets users browse and link their S3 (or compatible) bucket to resolve media references.
*   Optionally accepts LUT files (.cube, .3dl) per clip or globally for color grading.
*   Submits a render job (custom Python + FFmpeg) to Render.com background workers.
*   Delivers the finished render back to the user's cloud storage or as a direct download.

# **Architecture**

## **Components**

**Component**

**Technology**

**Purpose**

Web UI

React / FastAPI

Upload, media linking, LUT attachment, job status

Format Conversion

OTIO + adapters (AAF, FCPXML, xmeml)

Convert NLE formats to .otio

Media Resolver

boto3 / S3-compatible API

Browse bucket, map media references to signed cloud URLs

Color Pipeline

CDL (from AAF/XML) + optional LUT

Carry grade through to render

Render Engine

Custom Python OTIO-to-FFmpeg generator (subprocess + filter\_complex)

Timeline rendering, signed-URL inputs, and LUT application via FFmpeg filters

Job Queue

Render.com worker service (polls API)

Async render job claim, execution, progress heartbeats, and completion

Output Storage

S3 / Azure Blob / user-configured

Store finished renders

## **Render Job Flow**

*   User uploads an OTIO timeline (or, in the future, XML/AAF/FCPXML for backend conversion) plus optional LUT files via web UI.
*   Backend conversion to OTIO is planned but not implemented yet; users currently supply `.otio` directly.
*   User browses their S3 bucket; media references resolved to signed HTTPS URLs.
*   LUT files optionally linked per clip or timeline-wide.
*   Job submitted to the Render.com worker service, which polls the API for work.
*   Worker runs the shared BaseRender renderer: walks the OTIO timeline, builds FFmpeg `filter_complex` (cuts, dissolves via `xfade`, LUTs via `lut3d`), passes signed URLs directly as inputs, reports encode progress through worker heartbeats, and renders output.
*   Finished file written to user's configured output bucket or download URL.

# **Color Handling**

Color grading in the current implementation is LUT-based. CDL extraction and application from AAF/XML exports are planned for a later version.

**Layer**

**Source**

**How Applied**

**Editability**

**Status**

CDL (primary grade)

Embedded in AAF or XML export

Automatic — parsed from OTIO metadata; applied via FFmpeg color filters

Editable (slope/offset/power)

Planned

LUT (creative look)

User uploads .cube or .3dl file

Optional — linked via UI per clip or globally; applied via lut3d filter

Fixed (baked transform)

Implemented

When CDL support lands, the intended order is CDL first (correction), LUT second (look). Today, if no LUT is provided, the render proceeds with source color.

# **Deployment**

Designed for zero-ops self-hosting:

*   User forks the GitHub repository.
*   Connects repo to Render.com — web service + background worker auto-configured via render.yaml.
*   Sets environment variables: S3 bucket credentials, output path, optional webhook URL.
*   No Docker knowledge, no server management, no AWS account required beyond S3.

The job execution layer is abstracted behind worker/API endpoints today. A future `SubmitRenderJob()` interface could allow alternative backends (local FFmpeg, AWS Lambda) to be swapped in via config without changing application code.

# **v1 Scope**

**In Scope**

**Out of Scope**

Premiere Pro XML (xmeml) import

Complex effects / motion graphics

FCPXML import

Nested sequences / dynamic link

AAF import with CDL extraction

Secondary color corrections / power windows

S3 media linking (signed URLs)

Azure / GCS storage (v2)

Global and per-clip LUT application

Real-time preview

FFmpeg render via Render.com worker service (shared BaseRender renderer)

Collaborative editing

Basic transitions (cuts, dissolves via xfade)

Speed ramps / retiming

# **Open Source Dependencies**

*   OpenTimelineIO — timeline interchange format and Python API (Apache 2.0)
*   otio-aaf-adapter — AAF import adapter (Apache 2.0)
*   FFmpeg — video encoding, filter\_complex, xfade, lut3d, color filters (LGPL)
*   FastAPI — backend web framework (MIT)
*   boto3 — S3 media resolution and signed URL generation (Apache 2.0)

# **Known Limitations**

*   Complex Premiere effects (adjustment layers, plugins, advanced color) will not survive conversion — this is an editorial/rough-cut render tool, not a finishing tool.
*   LUT application is a baked transform — downstream editability is limited.
*   Render.com background jobs have compute time limits — very long timelines may need chunking.
*   Custom filter\_complex graph must be maintained for new transition/color effects.