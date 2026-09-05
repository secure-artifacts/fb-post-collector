import json
import re
import base64
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlparse


TAHOE_ROOT_DOC_ID = "7503499166326954"
TAHOE_ROOT_FRIENDLY_NAME = "CometTahoeRootQuery"
EDIT_HISTORY_DOC_ID = "24185759717674204"
EDIT_HISTORY_FRIENDLY_NAME = "CometFeedUnitEditHistoryDialogQuery"
SINGLE_POST_DOC_ID = "26637791682523429"
SINGLE_POST_FRIENDLY_NAME = "CometSinglePostDialogContentQuery"
PROFILE_TIMELINE_DOC_ID = "26199918962952281"
PROFILE_TIMELINE_FRIENDLY_NAME = "ProfileCometTimelineFeedRefetchQuery"
POST_TYPE_IMAGE = "\u56fe\u6587\u8d34"
POST_TYPE_VIDEO = "\u77ed\u89c6\u9891"
POST_TYPE_TEXT_CARD = "\u5f69\u8d34"
POST_TYPE_COMPOSITE = "\u5408\u6210\u56fe"
REPOST = "\u8f6c\u8d34"
ORIGINAL = "\u539f\u521b"


def parse_json_payloads(text: str) -> list[dict[str, Any]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if cleaned.startswith("for (;;);"):
        cleaned = cleaned[len("for (;;);") :].strip()
    if cleaned.startswith("{"):
        try:
            return [json.loads(cleaned)]
        except json.JSONDecodeError:
            pass

    payloads = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("for (;;);"):
            line = line[len("for (;;);") :].strip()
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            payloads.append(item)
    return payloads


def first_value(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def nested(data: Any, path: str, default=""):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
        if current is None:
            return default
    return current


def walk_dicts(data: Any):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from walk_dicts(value)
    elif isinstance(data, list):
        for item in data:
            yield from walk_dicts(item)


def timestamp_to_text(value):
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(value)


def extract_video_id(url: str, html: str = "") -> str:
    source = f"{url}\n{html}"
    patterns = [
        r"/reel/(\d+)",
        r"/videos/(\d+)",
        r"[?&]v=(\d+)",
        r'"videoID"\s*:\s*"(\d+)"',
        r'"video_id"\s*:\s*"(\d+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return ""


def extract_story_id(url: str, html: str = "") -> str:
    source = unescape(f"{url}\n{html}")
    patterns = [
        r'"storyID"\s*:\s*"([^"]+)"',
        r'"story_id"\s*:\s*"([^"]+)"',
        r'"feedbackTargetID"\s*:\s*"([^"]+)"',
        r"storyID=([^&#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1).replace("\\/", "/")
    return infer_story_id_from_url(url)


def infer_story_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    story_fbid = first_query_value(params, "story_fbid") or first_query_value(params, "fbid")
    actor_id = first_query_value(params, "id")
    if story_fbid and actor_id:
        return encode_story_id(actor_id, story_fbid)

    match = re.search(r"/groups/(\d+)/(?:posts|permalink)/(\d+)", parsed.path)
    if match:
        return encode_story_id(match.group(1), match.group(2))

    match = re.search(r"/(\d+)_(\d+)", parsed.path)
    if match:
        return encode_story_id(match.group(1), match.group(2))

    match = re.search(r"/(?:posts|permalink)/(\d+)", parsed.path)
    if match and actor_id:
        return encode_story_id(actor_id, match.group(1))
    return ""


def first_query_value(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def encode_story_id(actor_id: str, post_id: str) -> str:
    raw = f"S:_I{actor_id}:VK:{post_id}"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def single_post_variables(story_id: str) -> dict[str, Any]:
    return {
        "feedbackSource": 2,
        "feedLocation": "POST_PERMALINK_DIALOG",
        "focusCommentID": None,
        "privacySelectorRenderLocation": "COMET_STREAM",
        "renderLocation": "permalink",
        "scale": 1,
        "shouldChangeNodeFieldName": True,
        "storyID": story_id,
        "useDefaultActor": False,
        "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": False,
        "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": False,
        "__relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider": False,
        "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
        "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
        "__relay_internal__pv__IsWorkUserrelayprovider": False,
        "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
        "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": True,
        "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": True,
        "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
        "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
        "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
        "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
        "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
        "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
        "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": False,
        "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": True,
        "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
        "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": True,
        "__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider": 206,
        "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
        "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": True,
    }


def profile_timeline_variables(profile_id: str, after_time: int, before_time: int, cursor: str | None = None) -> dict[str, Any]:
    return {
        "afterTime": after_time,
        "beforeTime": before_time,
        "count": 3,
        "cursor": cursor,
        "feedLocation": "TIMELINE",
        "feedbackSource": 0,
        "focusCommentID": None,
        "memorializedSplitTimeFilter": None,
        "omitPinnedPost": True,
        "postedBy": {"group": "OWNER"},
        "privacy": None,
        "privacySelectorRenderLocation": "COMET_STREAM",
        "referringStoryRenderLocation": None,
        "renderLocation": "timeline",
        "scale": 1,
        "stream_count": 1,
        "taggedInOnly": None,
        "trackingCode": None,
        "useDefaultActor": False,
        "id": profile_id,
        "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": False,
        "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": False,
        "__relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider": False,
        "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
        "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
        "__relay_internal__pv__IsWorkUserrelayprovider": False,
        "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
        "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": True,
        "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": True,
        "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
        "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
        "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
        "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
        "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
        "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
        "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": False,
        "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": True,
        "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
        "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": True,
        "__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider": 206,
        "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
        "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": True,
    }


def story_edges_from_edit_history(payloads: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    for payload in payloads:
        node = nested(payload, "data.node", {})
        edges = nested(node, "edit_history.edges", [])
        if isinstance(node, dict) and isinstance(edges, list) and edges:
            return node, [edge.get("node", {}) for edge in edges if isinstance(edge, dict)]
    return {}, []


def extract_edit_history_fields(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    node, edits = story_edges_from_edit_history(payloads)
    if not node:
        return {}

    actor = nested(node, "actors.0", {}) if isinstance(node.get("actors"), list) else {}
    current = max(edits, key=lambda item: item.get("time") or 0) if edits else {}
    original = min(edits, key=lambda item: item.get("time") or 0) if edits else {}
    media = current.get("updated_media") or original.get("updated_media") or []
    media_url = media[0] if media else ""
    message = nested(current, "message.text") or nested(original, "message.text")
    reaction_status = current.get("reaction_status")

    return {
        "author_id": actor.get("id", ""),
        "author_name": actor.get("name", ""),
        "author_avatar": nested(actor, "profile_picture.uri"),
        "author_url": actor.get("url", ""),
        "media_url": media_url,
        "post_text": message or "",
        "post_time": timestamp_to_text(original.get("time") or current.get("time")),
        "like_count": reaction_status if reaction_status not in (None, "") else "",
        "post_id": current.get("id") or original.get("id") or node.get("id", ""),
        "post_type": POST_TYPE_IMAGE if media_url else POST_TYPE_TEXT_CARD,
        "repost_or_original": ORIGINAL,
        "is_edited": "是" if len(edits) > 1 else "否",
        "graphql_source": "edit_history",
        "graphql_confidence": {
            "like_count": "low",
            "comment_count": "missing",
            "share_count": "missing",
        },
    }


def first_nested(data: Any, paths: list[str], default=""):
    for path in paths:
        value = nested(data, path, "")
        if value not in (None, ""):
            return value
    return default


def first_nested_in(sources: list[Any], paths: list[str], default=""):
    for source in sources:
        value = first_nested(source, paths, "")
        if value not in (None, ""):
            return value
    return default


def first_count_object(data: Any, object_key: str, count_keys: tuple[str, ...] = ("count",)):
    for item in walk_dicts(data):
        value = item.get(object_key)
        if isinstance(value, dict):
            for count_key in count_keys:
                count = value.get(count_key)
                if count not in (None, ""):
                    return count
        elif value not in (None, "") and object_key.endswith("_count"):
            return value
    return ""


def first_story_attachment_image(story: dict[str, Any]) -> str:
    attachments = story.get("attachments") or []
    for attachment in attachments:
        uri = find_attachment_image(attachment)
        if uri:
            return uri
    return ""


def looks_like_content_image(data: dict[str, Any]) -> bool:
    typename = data.get("__typename") or data.get("__isMedia") or data.get("__isNode")
    return typename in {"Photo", "Image", "CometPhoto", "StoryAttachmentMedia"}


def find_attachment_image(data: Any) -> str:
    if isinstance(data, dict):
        uri = first_nested(
            data,
            [
                "styles.attachment.media.photo_image.uri",
                "styles.attachment.media.image.uri",
                "styles.attachment.media.viewer_image.uri",
                "styles.attachment.media.large_share_image.uri",
                "media.photo_image.uri",
                "media.image.uri",
                "media.viewer_image.uri",
                "media.large_share_image.uri",
                "photo_image.uri",
                "image.uri",
                "viewer_image.uri",
                "large_share_image.uri",
            ],
        )
        if uri and (looks_like_content_image(data) or "media" in data or "styles" in data):
            return uri
        for key in ("all_subattachments", "subattachments", "attachments", "attachment", "attached_story"):
            found = find_attachment_image(data.get(key))
            if found:
                return found
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = find_attachment_image(value)
                if found:
                    return found
    elif isinstance(data, list):
        for item in data:
            found = find_attachment_image(item)
            if found:
                return found
    return ""


def has_text_card_style(data: Any) -> bool:
    if isinstance(data, dict):
        for key, value in data.items():
            key_lower = str(key).lower()
            value_text = str(value).lower() if isinstance(value, str) else ""
            if key_lower in {"text_format_metadata", "text_format_preset_id", "background_color", "gradient", "story_card_info"}:
                return True
            if "background" in key_lower and value not in (None, "", [], {}):
                return True
            if "textstory" in value_text or "comet_feed_story_text" in value_text:
                return True
        return any(has_text_card_style(value) for value in data.values() if isinstance(value, (dict, list)))
    if isinstance(data, list):
        return any(has_text_card_style(item) for item in data)
    return False


def single_post_type(video_media: dict[str, Any], media_url: str, *stories: Any) -> str:
    if video_media:
        return POST_TYPE_VIDEO
    if media_url and any(has_text_card_style(story) for story in stories if isinstance(story, dict)):
        return POST_TYPE_COMPOSITE
    if media_url:
        return POST_TYPE_IMAGE
    return POST_TYPE_TEXT_CARD


def find_video_media(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        typename = data.get("__typename") or data.get("__isMedia")
        has_video_url = any(data.get(key) for key in ("playable_url", "browser_native_hd_url", "browser_native_sd_url"))
        if typename == "Video" or has_video_url:
            return data
        for value in data.values():
            found = find_video_media(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_video_media(item)
            if found:
                return found
    return {}


def first_attachment_video(*stories: Any) -> dict[str, Any]:
    for story in stories:
        if not isinstance(story, dict):
            continue
        attachment_groups = [
            story.get("attachments") or [],
            nested(story, "comet_sections.content.story.attachments", []),
        ]
        for attachments in attachment_groups:
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                video = find_video_media(attachment)
                if video:
                    return video
    return {}


def video_thumbnail_url(video: dict[str, Any]) -> str:
    return first_nested(
        video,
        [
            "preferred_thumbnail.image.uri",
            "thumbnailImage.uri",
            "image.uri",
            "photo_image.uri",
            "large_share_image.uri",
        ],
    )


def extract_single_post_fields(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    node = {}
    for payload in payloads:
        candidate = nested(payload, "data.node_v2", {})
        if isinstance(candidate, dict) and candidate.get("__typename") == "Story":
            node = candidate
            break
    if not node:
        return {}

    content_story = first_nested(
        node,
        [
            "comet_sections.content.story",
            "comet_sections.content.story.comet_sections.message.story",
        ],
        {},
    )
    actor = first_nested(
        node,
        [
            "comet_sections.content.story.actors.0",
            "comet_sections.context_layout.story.comet_sections.actor_photo.story.actors.0",
        ],
        {},
    )
    feedback_target = nested(
        node,
        "comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context",
        {},
    )
    summary = nested(feedback_target, "comet_ufi_summary_and_actions_renderer.feedback", {})
    comment_feedback = nested(feedback_target, "comment_list_renderer.feedback", {})
    creation_time = first_nested(
        node,
        [
            "comet_sections.context_layout.story.comet_sections.metadata.1.story.creation_time",
            "comet_sections.context_layout.story.comet_sections.metadata.0.story.creation_time",
        ],
    )
    video_media = first_attachment_video(node, content_story)
    video_url = first_value(
        video_media.get("browser_native_hd_url", "") if isinstance(video_media, dict) else "",
        video_media.get("browser_native_sd_url", "") if isinstance(video_media, dict) else "",
        video_media.get("playable_url", "") if isinstance(video_media, dict) else "",
    )
    video_id = video_media.get("id", "") if isinstance(video_media, dict) else ""
    media_url = video_thumbnail_url(video_media) or first_nested(
        node,
        [
            "attachments.0.styles.attachment.media.photo_image.uri",
            "comet_sections.content.story.attachments.0.styles.attachment.media.photo_image.uri",
        ],
    ) or first_story_attachment_image(node) or first_story_attachment_image(content_story if isinstance(content_story, dict) else {}) or find_attachment_image(content_story)
    image_accessibility_text = first_nested(
        node,
        [
            "attachments.0.styles.attachment.media.accessibility_caption",
            "comet_sections.content.story.attachments.0.styles.attachment.media.accessibility_caption",
        ],
    )

    like_count = first_nested(
        summary,
        [
            "adaptive_ufi_action_renderers.0.feedback.reaction_count.count",
            "feedback.reaction_count.count",
            "reaction_count.count",
        ],
    )
    comment_count = first_nested(
        comment_feedback,
        [
            "comment_rendering_instance.comments.total_count",
            "comment_rendering_instance_for_feed_location.comments.total_count",
            "comment_rendering_instance_for_feed_location.comments.count",
        ],
    ) or first_nested(
        summary,
        [
            "adaptive_ufi_action_renderers.1.feedback.comment_rendering_instance.comments.total_count",
            "comment_rendering_instance.comments.total_count",
        ],
    )
    share_count = first_nested(
        summary,
        [
            "adaptive_ufi_action_renderers.2.feedback.share_count.count",
            "adaptive_ufi_action_renderers.2.feedback.i18n_share_count",
            "share_count.count",
            "i18n_share_count",
        ],
    )

    return {
        "author_id": actor.get("id", "") if isinstance(actor, dict) else "",
        "author_name": actor.get("name", "") if isinstance(actor, dict) else "",
        "author_avatar": nested(actor, "profile_picture.uri") if isinstance(actor, dict) else "",
        "author_url": first_value(actor.get("url"), actor.get("profile_url")) if isinstance(actor, dict) else "",
        "media_url": media_url,
        "image_accessibility_text": image_accessibility_text,
        "post_text": first_nested(node, ["comet_sections.content.story.message.text", "comet_sections.content.story.comet_sections.message.story.message.text", "message.text"]),
        "post_time": timestamp_to_text(creation_time),
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "post_id": first_value(node.get("post_id"), node.get("id"), video_id),
        "post_type": single_post_type(video_media, media_url, node, content_story),
        "repost_or_original": REPOST if node.get("attached_story") else ORIGINAL,
        "original_post_url": nested(node, "attached_story.url"),
        "video_id": video_id,
        "video_url": video_url,
        "captions_url": nested(video_media, "video_available_captions_locales.0.captions_url") if isinstance(video_media, dict) else "",
        "graphql_source": "single_post",
    }


def timeline_edges_and_page_info(payloads: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    page_info: dict[str, Any] = {}
    for payload in payloads:
        feed = nested(payload, "data.node.timeline_list_feed_units", {})
        if isinstance(feed, dict):
            for edge in feed.get("edges") or []:
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                    edges.append(edge)
        info = nested(payload, "data.page_info", {})
        if isinstance(info, dict) and info:
            page_info = info
    return edges, page_info


def first_story_url(story: dict[str, Any], actor_id: str = "") -> str:
    for item in walk_dicts(story):
        url = item.get("permalink_url") or item.get("url")
        if isinstance(url, str):
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").lower()
            if parsed.scheme == "https" and (hostname == "facebook.com" or hostname.endswith(".facebook.com")):
                return url
    tracking = story.get("tracking")
    if tracking:
        try:
            data = json.loads(tracking)
        except Exception:
            data = {}
        post_id = first_value(data.get("top_level_post_id"), data.get("story_fbid"), data.get("mf_story_key"))
        owner_id = first_value(actor_id, data.get("profile_id"), data.get("content_owner_id_new"))
        if post_id and owner_id:
            return f"https://www.facebook.com/story.php?story_fbid={post_id}&id={owner_id}"
    return ""


def first_creation_time(story: dict[str, Any]) -> Any:
    value = first_nested(
        story,
        [
            "comet_sections.context_layout.story.comet_sections.metadata.1.story.creation_time",
            "comet_sections.context_layout.story.comet_sections.metadata.0.story.creation_time",
            "creation_time",
            "publish_time",
        ],
    )
    if value:
        return value
    for item in walk_dicts(story):
        value = item.get("creation_time") or item.get("publish_time")
        if value:
            return value
    return ""


def extract_story_fields(story: dict[str, Any]) -> dict[str, Any]:
    content_story = first_nested(
        story,
        [
            "comet_sections.content.story",
            "comet_sections.content.story.comet_sections.message.story",
        ],
        {},
    )
    actor = first_nested(
        story,
        [
            "comet_sections.content.story.actors.0",
            "comet_sections.context_layout.story.comet_sections.actor_photo.story.actors.0",
            "actors.0",
        ],
        {},
    )
    feedback_target = nested(
        story,
        "comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context",
        {},
    )
    summary = nested(feedback_target, "comet_ufi_summary_and_actions_renderer.feedback", {})
    comment_feedback = nested(feedback_target, "comment_list_renderer.feedback", {})
    video_media = first_attachment_video(story, content_story)
    video_url = first_value(
        video_media.get("browser_native_hd_url", "") if isinstance(video_media, dict) else "",
        video_media.get("browser_native_sd_url", "") if isinstance(video_media, dict) else "",
        video_media.get("playable_url", "") if isinstance(video_media, dict) else "",
        video_media.get("url", "") if isinstance(video_media, dict) else "",
    )
    video_id = video_media.get("id", "") if isinstance(video_media, dict) else ""
    media_url = video_thumbnail_url(video_media) or first_story_attachment_image(story) or find_attachment_image(story)
    creation_time = first_creation_time(story)
    actor_id = actor.get("id", "") if isinstance(actor, dict) else ""
    post_url = first_story_url(story, actor_id)

    metric_sources = [summary, comment_feedback, feedback_target, content_story, story]
    like_count = first_nested_in(
        metric_sources,
        [
            "feedback.reaction_count.count",
            "reaction_count.count",
            "comet_ufi_summary_and_actions_renderer.feedback.reaction_count.count",
            "i18n_reaction_count",
        ],
    ) or first_count_object(story, "reaction_count")
    comment_count = first_nested_in(
        metric_sources,
        [
            "comments.total_count",
            "comments.count",
            "comment_rendering_instance.comments.total_count",
            "comment_rendering_instance_for_feed_location.comments.total_count",
            "comment_rendering_instance_for_feed_location.comments.count",
            "comet_ufi_summary_and_actions_renderer.feedback.comments.total_count",
        ],
    ) or first_count_object(story, "comments", ("total_count", "count"))
    share_count = first_nested_in(
        metric_sources,
        [
            "share_count.count",
            "feedback.share_count.count",
            "comet_ufi_summary_and_actions_renderer.feedback.share_count.count",
            "i18n_share_count",
        ],
    ) or first_count_object(story, "share_count")

    return {
        "post_url": post_url,
        "page_id": actor_id,
        "author_id": actor_id,
        "author_name": actor.get("name", "") if isinstance(actor, dict) else "",
        "author_avatar": nested(actor, "profile_picture.uri") if isinstance(actor, dict) else "",
        "author_url": first_value(actor.get("url"), actor.get("profile_url")) if isinstance(actor, dict) else "",
        "media_url": media_url,
        "image_accessibility_text": first_nested(
            story,
            [
                "attachments.0.styles.attachment.media.accessibility_caption",
                "comet_sections.content.story.attachments.0.styles.attachment.media.accessibility_caption",
            ],
        ),
        "post_text": first_nested(
            story,
            [
                "comet_sections.content.story.message.text",
                "comet_sections.content.story.comet_sections.message.story.message.text",
                "message.text",
            ],
        ),
        "post_time": timestamp_to_text(creation_time),
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "post_id": first_value(story.get("post_id"), story.get("id"), video_id),
        "post_type": single_post_type(video_media, media_url, story, content_story),
        "repost_or_original": REPOST if story.get("attached_story") else ORIGINAL,
        "original_post_url": nested(story, "attached_story.url"),
        "video_id": video_id,
        "video_url": video_url,
        "captions_url": nested(video_media, "video_available_captions_locales.0.captions_url") if isinstance(video_media, dict) else "",
        "video_view_count": first_nested(video_media, ["feedback.video_view_count", "view_count"]) if isinstance(video_media, dict) else "",
        "graphql_source": "profile_timeline",
        "creation_time_unix": int(creation_time) if str(creation_time).isdigit() else "",
    }


def find_tahoe_video(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    for payload in payloads:
        video = nested(payload, "data.tahoe_sidepane_renderer.video", {})
        if isinstance(video, dict) and video:
            return video
    for payload in payloads:
        video = nested(payload, "data.video", {})
        if isinstance(video, dict) and video:
            return video
    return {}


def extract_reel_fields(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    video = find_tahoe_video(payloads)
    if not video:
        return {}

    owner = video.get("owner") or {}
    creation_story = video.get("creation_story") or {}
    actor = nested(creation_story, "comet_sections.title.story.actors.0", {})
    feedback = video.get("feedback") or {}
    thumbnail = first_value(
        nested(video, "preferred_thumbnail.image.uri"),
        nested(video, "thumbnailImage.uri"),
        nested(video, "image.uri"),
    )
    post_text = first_value(
        nested(video, "creation_story.message.text"),
        nested(creation_story, "message.text"),
        nested(video, "message.text"),
        video.get("text"),
    )

    return {
        "author_id": first_value(owner.get("id"), actor.get("id")),
        "author_name": first_value(owner.get("name"), actor.get("name")),
        "author_avatar": first_value(nested(owner, "owner_as_page.profile_pic_uri"), nested(actor, "profile_picture.uri")),
        "author_url": first_value(actor.get("url"), owner.get("url")),
        "media_url": thumbnail,
        "post_text": post_text or "",
        "post_time": timestamp_to_text(video.get("publish_time") or nested(creation_story, "publish_time")),
        "like_count": nested(feedback, "reaction_count.count"),
        "comment_count": feedback.get("total_comment_count", ""),
        "share_count": nested(feedback, "share_count.count"),
        "post_id": video.get("id", ""),
        "post_type": POST_TYPE_VIDEO,
        "video_view_count": feedback.get("video_view_count", ""),
        "repost_or_original": ORIGINAL,
        "video_url": first_value(video.get("browser_native_hd_url"), video.get("browser_native_sd_url")),
        "captions_url": nested(video, "video_available_captions_locales.0.captions_url"),
        "graphql_source": "tahoe",
    }


def merge_non_empty(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def jazoest_from_token(token: str) -> str:
    if not token:
        return ""
    return "2" + "".join(str(ord(char)) for char in token)


def graphql_fetch_script():
    return r"""
const [friendlyName, docId, variables, callback] = arguments;
function getCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : '';
}
function byRequire(moduleName, key) {
  try {
    if (typeof require === 'function') {
      const mod = require(moduleName);
      return mod && mod[key] ? mod[key] : '';
    }
  } catch (error) {}
  return '';
}
const fbDtsg = byRequire('DTSGInitialData', 'token') || document.querySelector('[name="fb_dtsg"]')?.value || '';
const lsd = byRequire('LSD', 'token') || document.querySelector('[name="lsd"]')?.value || '';
const user = getCookie('c_user');
if (!user) {
  callback({ok: false, status: 0, text: 'NO_C_USER'});
  return;
}
const body = new FormData();
body.append('av', user);
body.append('__user', user);
body.append('__a', '1');
body.append('dpr', String(window.devicePixelRatio || 1));
body.append('__comet_req', '15');
body.append('server_timestamps', 'true');
body.append('fb_api_caller_class', 'RelayModern');
body.append('fb_api_req_friendly_name', friendlyName);
body.append('variables', JSON.stringify(variables));
body.append('doc_id', docId);
if (fbDtsg) body.append('fb_dtsg', fbDtsg);
if (lsd) body.append('lsd', lsd);
if (fbDtsg) body.append('jazoest', '2' + Array.from(fbDtsg).map(ch => ch.charCodeAt(0)).join(''));
fetch('/api/graphql/', {
  method: 'POST',
  credentials: 'include',
  headers: {
    'accept': '*/*',
    'x-fb-friendly-name': friendlyName,
    ...(lsd ? {'x-fb-lsd': lsd} : {})
  },
  body
}).then(async response => {
  callback({ok: response.ok, status: response.status, text: await response.text()});
}).catch(error => {
  callback({ok: false, status: 0, text: String(error && error.stack || error)});
});
"""
