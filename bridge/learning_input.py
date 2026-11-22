"""Build Learning prompts from raw WebSocket frames in recorded sessions."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterator

import yaml

from .network_events import NetworkSequenceReconstructor


_DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "learning_input.yaml"


@dataclass(frozen=True)
class LearningInputSource:
    """Off-prompt source link for one rendered prompt row."""

    prompt_line_index: int
    event_id: str
    connection_id: str
    stream_id: str
    stream_sequence: int
    ingest_sequence: int
    direction: str
    connection_alias: str = ""


@dataclass(frozen=True)
class LearningPromptInput:
    """Rendered raw-frame prompt plus provenance kept outside the prompt."""

    prompt_text: str
    source_map: tuple[LearningInputSource, ...]
    capture_segment_id: str | None

    @property
    def frame_count(self) -> int:
        return len(self.source_map)


def build_learning_input(
    session_path: str | Path,
    capture_segment_id: str | None = None,
    contract_path: str | Path | None = None,
) -> LearningPromptInput:
    """Select and render raw Socket.IO WebSocket frames for a Learning prompt.

    HTTP events remain in the JSONL session but are filtered before prompt
    construction. Payload text is copied as recorded; this function does not
    call the Socket.IO decoder or infer poker semantics from frame contents.
    """
    contract = _load_contract(contract_path or _DEFAULT_CONTRACT_PATH)
    _validate_contract(contract)

    source_cfg = contract["source"]
    envelope_key = source_cfg["event_envelope_key"]
    segment_type = source_cfg["segment_record_type"]
    segment_id_field = source_cfg["capture_segment_id_field"]
    refs_key = source_cfg["segment_event_refs_key"]
    ref_id_key = source_cfg["segment_event_ref_key"]

    referenced_ids: set[str] | None = None
    if capture_segment_id is not None:
        referenced_ids = _resolve_segment_refs(
            session_path,
            envelope_key=envelope_key,
            segment_type=segment_type,
            segment_id_field=segment_id_field,
            capture_segment_id=str(capture_segment_id),
            refs_key=refs_key,
            ref_id_key=ref_id_key,
        )

    selection = contract["selection"]
    selected_events: list[dict[str, Any]] = []
    seen_raw_event_ids: set[str] = set()
    for event in _iter_session_events(session_path, envelope_key):
        if event.get("event_type") != selection["event_type"]:
            continue

        event_id = str(event.get("event_id", ""))
        if event_id:
            seen_raw_event_ids.add(event_id)
        if referenced_ids is not None and event_id not in referenced_ids:
            continue
        if (
            event.get("stream_type") != selection["stream_type"]
            or event.get("kind") != selection["kind"]
            or event.get("direction") not in selection["directions"]
        ):
            continue

        payload = event.get(contract["payload"]["field"])
        if not isinstance(payload, str):
            raise ValueError(
                f"WebSocket frame {event_id or '<unknown>'} has non-string payload"
            )
        if "\n" in payload or "\r" in payload:
            raise ValueError(
                f"WebSocket frame {event_id or '<unknown>'} contains a line break; "
                "the raw one-frame-per-line prompt format cannot represent it"
            )
        if not event_id:
            raise ValueError("WebSocket frame is missing event_id provenance")
        if "stream_sequence" not in event or "ingest_sequence" not in event:
            raise ValueError(f"WebSocket frame {event_id} is missing sequence metadata")

        selected_events.append(event)

    if referenced_ids is not None:
        missing_refs = referenced_ids - seen_raw_event_ids
        if missing_refs:
            sample = ", ".join(sorted(missing_refs)[:3])
            raise ValueError(
                f"capture segment references {len(missing_refs)} missing raw events"
                + (f": {sample}" if sample else "")
            )

    if not selected_events:
        raise ValueError("no raw WebSocket frames matched the Learning input contract")

    ordered_events = NetworkSequenceReconstructor.reconstruct(selected_events)
    connection_keys = [
        _connection_key(event) for event in ordered_events
    ]
    aliases = _connection_aliases(connection_keys)
    use_aliases = len(aliases) > 1

    prompt_cfg = contract["prompt"]
    line_format = (
        prompt_cfg["multi_connection_line_format"]
        if use_aliases
        else prompt_cfg["line_format"]
    )
    lines: list[str] = []
    source_map: list[LearningInputSource] = []

    for line_index, event in enumerate(ordered_events, start=1):
        connection_id = str(event.get("connection_id", ""))
        stream_id = str(event.get("stream_id", ""))
        connection_alias = aliases[_connection_key(event)] if use_aliases else ""
        stream_sequence = int(event["stream_sequence"])
        ingest_sequence = int(event["ingest_sequence"])
        direction = str(event["direction"])
        payload = event[contract["payload"]["field"]]

        lines.append(
            line_format.format(
                stream_sequence=stream_sequence,
                direction=direction,
                payload=payload,
                connection_alias=connection_alias,
            )
        )
        source_map.append(
            LearningInputSource(
                prompt_line_index=line_index,
                event_id=str(event["event_id"]),
                connection_id=connection_id,
                stream_id=stream_id,
                stream_sequence=stream_sequence,
                ingest_sequence=ingest_sequence,
                direction=direction,
                connection_alias=connection_alias,
            )
        )

    return LearningPromptInput(
        prompt_text="\n".join(lines),
        source_map=tuple(source_map),
        capture_segment_id=(
            str(capture_segment_id) if capture_segment_id is not None else None
        ),
    )


def _load_contract(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        contract = yaml.safe_load(file)
    if not isinstance(contract, dict):
        raise ValueError("Learning input YAML must contain a mapping")
    return contract


def _validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("contract", {}).get("name") != "raw_socketio_learning_input":
        raise ValueError("unsupported Learning input contract")
    if contract.get("contract", {}).get("version") != 1:
        raise ValueError("unsupported Learning input contract version")

    selection = contract.get("selection", {})
    payload = contract.get("payload", {})
    prompt = contract.get("prompt", {})
    provenance = contract.get("provenance", {})
    semantic_labeling = contract.get("semantic_labeling", {})
    source = contract.get("source", {})

    if source.get("format") != "session_jsonl":
        raise ValueError("Learning input source must be a recorded session JSONL")
    if (
        selection.get("event_type") != "raw_network_event"
        or selection.get("stream_type") != "websocket"
        or selection.get("kind") != "frame"
        or selection.get("include_http") is not False
    ):
        raise ValueError("Learning input must select WebSocket events and exclude HTTP")
    if selection.get("include_all_matching_segment_refs") is not True:
        raise ValueError("all WebSocket frames in the capture segment must be retained")
    if payload.get("decode_socketio") is not False:
        raise ValueError("Learning input must use raw frames without Socket.IO decoding")
    if payload.get("use_as_recorded") is not True or payload.get("normalize_or_rewrite") is not False:
        raise ValueError("raw WebSocket payloads must be passed through unchanged")
    if payload.get("include_control_frames") is not True:
        raise ValueError("raw input contract must retain all matching frame payloads")
    if payload.get("line_break_policy") != "reject_with_source_id":
        raise ValueError("unsupported raw payload line-break policy")
    if prompt.get("include_decoded_packets") is not False:
        raise ValueError("decoded packets are outside the Learning prompt contract")
    if prompt.get("format") != "one_frame_per_line":
        raise ValueError("unsupported Learning prompt format")
    if prompt.get("fields") != ["stream_sequence", "direction", "payload"]:
        raise ValueError("unsupported Learning prompt fields")
    if prompt.get("preserve_payload_text_verbatim") is not True:
        raise ValueError("raw payload text must be rendered verbatim")
    if prompt.get("payload_is_remainder_of_line") is not True:
        raise ValueError("raw payload must remain the final, unescaped line field")
    if prompt.get("include_connection_alias_only_when_multiple_connections") is not True:
        raise ValueError("connection aliases may only be added when needed")
    if provenance.get("keep_source_lookup_outside_prompt") is not True:
        raise ValueError("source provenance must stay outside prompt text")
    if provenance.get("prompt_line_index_to_event_id_map") is not True:
        raise ValueError("prompt rows must retain an off-prompt source lookup")
    if source.get("expose_segment_id_to_model") is not False:
        raise ValueError("capture segment IDs must not be exposed as poker labels")
    if semantic_labeling.get("prelabel_hand_starts_or_ends") is not False:
        raise ValueError("hand boundaries must not be pre-labeled")
    if semantic_labeling.get("trim_capture_segment_by_inferred_hand") is not False:
        raise ValueError("capture frames must not be trimmed by inferred hand boundaries")


def _resolve_segment_refs(
    session_path: str | Path,
    *,
    envelope_key: str,
    segment_type: str,
    segment_id_field: str,
    capture_segment_id: str,
    refs_key: str,
    ref_id_key: str,
) -> set[str]:
    matches = [
        event
        for event in _iter_session_events(session_path, envelope_key)
        if event.get("event_type") == segment_type
        and str(event.get(segment_id_field, "")) == capture_segment_id
    ]
    if not matches:
        raise KeyError(f"capture segment not found: {capture_segment_id}")
    if len(matches) > 1:
        raise ValueError(f"capture segment ID is not unique: {capture_segment_id}")

    refs = matches[0].get(refs_key, [])
    if not isinstance(refs, list):
        raise ValueError(f"capture segment {capture_segment_id} has invalid event refs")
    referenced_ids = {
        str(ref[ref_id_key])
        for ref in refs
        if isinstance(ref, dict) and ref.get(ref_id_key)
    }
    if len(referenced_ids) != len(refs):
        raise ValueError(f"capture segment {capture_segment_id} has an invalid event ref")
    return referenced_ids


def _iter_session_events(
    session_path: str | Path, envelope_key: str
) -> Iterator[dict[str, Any]]:
    with Path(session_path).open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSONL record at {session_path}:{line_number}"
                ) from exc
            event = row.get(envelope_key) if isinstance(row, dict) else None
            if isinstance(event, dict):
                yield event


def _connection_key(event: dict[str, Any]) -> str:
    return str(event.get("connection_id") or event.get("stream_id") or "unknown")


def _connection_aliases(connection_keys: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key in connection_keys:
        if key not in aliases:
            aliases[key] = f"c{len(aliases) + 1}"
    return aliases
