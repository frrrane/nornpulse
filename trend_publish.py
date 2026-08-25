#!/usr/bin/env python3
"""
Trend-driven generation: read what is trending, decide what this channel
could make about it, generate it, and publish it with the usual grounding.

    python trend_publish.py --channel sloptokdaily              # plan only
    python trend_publish.py --channel sloptokdaily --generate   # + make the video
    python trend_publish.py --channel sloptokdaily --generate --stage

Planning is free and is the default. **--generate bills per second of
generated video**, so it never happens implicitly: you have to ask. Check
current Veo pricing before running it in a loop.

Nothing reaches YouTube on its own. --stage emails the finished clip for
approval, which is how every other path on this project publishes;
check_approvals.py uploads it once you reply. --publish exists to bypass
that and says so when used.

Generated footage arrives with no hook text and often no useful audio, so
it gets a spoken line from Mímir and a burned-in hook before it goes
anywhere — without those it is the silent, contextless filler the phrase
"AI slop" was coined for.

Footage is generated from a text prompt, or taken from freely-licensed
archives with --source public_domain. It is never taken from the trending
videos themselves — that is a copyright claim against the channel, not a
shortcut.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("output_clips")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", default=None, help="channel slug (see channels.json)")
    ap.add_argument("--generate", action="store_true",
                    help="actually generate the video. Bills per second of output.")
    ap.add_argument("--stage", action="store_true",
                    help="email the result for approval instead of uploading. "
                         "This is the sanctioned path: nothing on this project "
                         "publishes itself. Requires --generate.")
    ap.add_argument("--publish", action="store_true",
                    help="upload directly, bypassing human review. Requires "
                         "--generate. Prefer --stage.")
    ap.add_argument("--no-finish", action="store_true",
                    help="skip the narration and burned-in hook")
    ap.add_argument("--no-guard", action="store_true",
                    help="skip the rights check. Only for material you know "
                         "you have the rights to.")
    ap.add_argument("--source", default="generated",
                    choices=["generated", "public_domain"])
    ap.add_argument("--model", default=None,
                    help="Veo variant. Default: the fast one.")
    ap.add_argument("--duration", type=int, default=8, help="seconds of video")
    ap.add_argument("--privacy", default="private",
                    choices=["private", "unlisted", "public"],
                    help="default private: a trend-driven clip should be looked "
                         "at before it is public")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    if (args.publish or args.stage) and not args.generate:
        print("❌ --stage/--publish need --generate; there would be nothing to send.")
        return 2

    from agent import channels as chans
    from agent import footage as fg
    from agent import trend_loop as tl
    from agent import trending_ingest as ti

    try:
        channel = chans.get_channel(args.channel)
    except KeyError as e:
        print(f"❌ {e}")
        return 1

    print(f"📺 {channel.slug} ({channel.title})")

    # 1. What is trending
    trending = ti.top_tags(limit=200)
    topics = tl.candidate_topics(trending)
    if not topics:
        print("❌ No usable trending topics. Run `python ingest_trending.py --regions US` "
              "to refresh the snapshot.")
        return 1
    summary = ti.snapshot_summary()
    when = summary["snapshot_at"] if summary else "unknown"
    print(f"📈 {len(topics)} candidate topics from the snapshot taken {when}")

    # 2. What to make of it
    print("🧠 Choosing a topic and writing a brief...")
    brief = tl.write_brief(channel, topics)
    if brief is None:
        print("🤷 Nothing trending suits this channel right now, so nothing was made.\n"
              "   That is a real answer — forcing a topic produces off-brand filler.")
        return 0

    print(f"\n  topic     {brief.topic}  "
          f"(measured: {brief.trend_videos} trending videos, "
          f"median {brief.trend_median_views:,.0f} views)")
    print(f"  angle     {brief.angle}")
    print(f"  title     {brief.title}")
    print(f"  caption   {brief.caption}")
    print(f"  hook      {brief.hook_type}")
    print(f"  why       {brief.rationale}")
    # The premises this one beat. A reviewer shown only the winner cannot
    # tell whether the choice was good, and the alternatives cost nothing —
    # they were written in the same call.
    alternatives = brief.extra.get("alternatives") or []
    if alternatives:
        reason = brief.extra.get("pick_reason", "")
        print(f"\n  chosen over {len(alternatives)} other premise"
              f"{'s' if len(alternatives) > 1 else ''}"
              f"{': ' + reason if reason else ''}")
        for other in alternatives:
            print(f"    · [{other['topic']}] {other['angle']}")

    print(f"\n  video prompt:\n    {brief.video_prompt}")
    if brief.negative_prompt:
        print(f"  avoiding:\n    {brief.negative_prompt}")

    # Shown before the generate step, because both of these were previously
    # found by a human watching the finished video — which is the expensive
    # way to learn that the last three seconds are a held pose.
    for warning in tl.brief_warnings(brief):
        print(f"  ⚠️  {warning}")

    # Rights check, before anything is generated. Placed here for two
    # reasons: the brief writer is shown this channel's own titles as a voice
    # reference and those lean heavily on other people's property, and a
    # refusal from Veo would cost a paid call to discover.
    if not args.no_guard:
        from agent import watchdog as wd
        verdict = wd.check_brief(brief)
        print()
        print(wd.describe(verdict))
        if verdict.blocked:
            print("\n⛔ Not generating. Re-run to get a different brief, or pass "
                  "--no-guard if you hold the rights.")
            return 1
        if verdict.needs_human and args.generate:
            print("\n⚠️  Flagged for a human look, so nothing was generated. "
                  "Re-run with --no-guard to proceed anyway.")
            return 1
    else:
        verdict = None
        print("\n⚪ rights check skipped (--no-guard)")

    if not args.generate:
        model = args.model or fg.DEFAULT_VEO_MODEL
        print(f"\n(plan only — nothing generated, nothing spent)")
        print(f" Add --generate to make this with {model} "
              f"({args.duration}s, 9:16). Billed per second: {fg.VEO_PRICING_URL}")
        return 0

    # 3. Make it
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    clip_id = f"trend_{channel.slug}_{stamp}"
    out_path = OUTPUT_DIR / f"{clip_id}.mp4"
    print(f"\n🎬 Generating via {args.source}... this takes a minute or more.")
    try:
        if args.source == "generated":
            shot = fg.generate_with_veo(
                brief.video_prompt, out_path,
                model=args.model or fg.DEFAULT_VEO_MODEL,
                duration_sec=args.duration,
                negative_prompt=brief.negative_prompt or None,
            )
        else:
            shot = fg.fetch_wikimedia(brief.topic, out_path)
    except fg.FootageError as e:
        print(f"❌ {e}")
        return 1

    size_mb = shot.path.stat().st_size / 1e6
    print(f"✅ {shot.path}  ({size_mb:.1f} MB, {shot.provider})")
    if shot.needs_attribution:
        print(f"   ⚠️  licence {shot.licence} requires credit: {shot.attribution}")

    # 3b. Finish it. Generated footage arrives with no hook text and often no
    # useful audio, which is the difference between a clip and filler.
    video_path = shot.path
    if not args.no_finish:
        from agent import shortsmith
        print("🎤 Adding narration and burning the hook...")
        finished = shortsmith.finish(shot.path, brief, clip_id)
        video_path = finished["path"]
        bits = []
        if finished["narrated"]:
            bits.append("narration")
        if finished["hook_burned"]:
            bits.append(f"hook {finished['hook']!r}")
        print(f"   {'+ ' + ', '.join(bits) if bits else 'nothing added'}"
              f"  -> {video_path.name}")

    if not args.publish and not args.stage:
        print(f"\n(nothing sent — inspect it, then re-run with --stage to email "
              f"it for approval)")
        print(f"   python publish_file.py {video_path} --title {brief.title!r} "
              f"--channel {channel.slug}")
        return 0

    # 4. Publish it through the same grounding as everything else
    from agent import calibration as cal
    from agent.norn_publisher import NornPublisher, PublishError
    from agent.urdr_analytics import UrdrAnalytics

    clip = brief.as_clip(clip_id)
    publisher = NornPublisher(channel)
    tags, decisions = publisher._tags_for(clip, brief.title, brief.caption)
    print(f"\n🏷️  tags: {', '.join(tags)}")

    forecast = cal.calibrated_forecast(channel, has_subtitles=False) or {}
    if forecast:
        print(f"📊 forecast p50 {forecast['p50']:,.0f} views "
              f"(p10-p90 {forecast['p10']:,.0f}-{forecast['p90']:,.0f})")

    if args.stage:
        # The sanctioned path. Write the sidecar first: check_approvals.py
        # looks the clip up by id when the reply comes back, and an approval
        # that cannot find its clip is a dead end.
        import json
        clip_record = dict(clip)
        clip_record.update({
            "output_video_path": str(video_path),
            "virality_score": 0.0,
            "trend_topic": brief.topic,
            "trend_videos": brief.trend_videos,
            "angle": brief.angle,
            "source": "generated",
            "footage_provider": shot.provider,
            "video_prompt": brief.video_prompt,
            "tags": tags,
        })
        if verdict is not None:
            clip_record["rights_check"] = (
                f"{verdict.level.upper()} — {verdict.summary()}")
            clip_record["rights_not_checked"] = ", ".join(verdict.checks_not_run)
        else:
            clip_record["rights_check"] = "skipped (--no-guard)"
        if not args.no_finish:
            clip_record["hook_burned"] = finished.get("hook") or ""
            clip_record["has_narration"] = finished.get("narrated", False)
        if forecast:
            clip_record["forecast_p50"] = f"{forecast['p50']:,.0f} views"
            clip_record["forecast_range"] = (
                f"{forecast['p10']:,.0f} - {forecast['p90']:,.0f} views")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        sidecar = OUTPUT_DIR / f"{clip_id}_metadata.json"
        sidecar.write_text(json.dumps(clip_record, indent=2), encoding="utf-8")

        print(f"\n📧 Emailing {clip_id} for approval...")
        ok = publisher.send_gmail_staged_approval(
            clip_id=clip_id, title=brief.title, virality=0.0,
            video_path=str(video_path), clip=clip_record)
        if not ok:
            print("❌ Could not send the staging email — see logs. The clip is "
                  f"still at {video_path}")
            return 1
        print("✅ sent. Reply APPROVE or REJECT, then run:")
        print(f"   python check_approvals.py --channel {channel.slug} "
              f"--privacy {args.privacy}")
        return 0

    print(f"\n⚠️  Uploading directly, without review. --stage is the reviewed path.")
    print(f"⬆️  Uploading as {args.privacy}...")
    try:
        res = publisher.upload_to_youtube_shorts(
            video_path=video_path, title=brief.title, description=brief.caption,
            privacy_status=args.privacy, clip=clip, source="generated")
    except PublishError as e:
        print(f"❌ {e}")
        return 1

    print(f"✅ {res['url']}")
    UrdrAnalytics().log_published_outcome(
        clip_id=clip_id, youtube_video_id=res["video_id"], youtube_url=res["url"],
        hook_type=brief.hook_type or "unknown",
        predicted_virality_score=0.0, predicted_3s_retention_pct=0.0,
        forecast_views_p50=float(forecast.get("p50", 0.0)),
        forecast_views_p90=float(forecast.get("p90", 0.0)),
        forecast_views_p10=float(forecast.get("p10", 0.0)),
    )
    print("   forecast logged — sync_stats.py will grade it once it matures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
