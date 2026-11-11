# Media transcription benchmark (2026-11-07)

Before/after measurement of one law-lecture recording and one short clip for the
media transcription change (`docs/04-EMBEDDING_AND_QUERY_SYSTEM.md`, "Media
transcription"): Whisper `turbo` through Docling's ASR pipeline, baked checkpoint,
own thread count, GPU, dedicated media workers.

## Setup

- Box: Minisforum (Intel Ultra 9 285HX, 20 cores visible to the container,
  NVIDIA RTX PRO 2000 Blackwell 16 GB), Docker Desktop. **The live ingestion job
  (16 conversion slots, office_lite profile) was running at the same time**, so the
  CPU figures are pessimistic; the GPU was otherwise idle (embedders on it).
- Container: the live image `recallhub-backend:latest` (docling 2.142.0, torch
  2.14.1+cu130) started on the side with `docker run --rm` (plus `--gpus all` for
  the CUDA runs), the CIFS share volumes `recallhub_share_akten` /
  `recallhub_share_literatur` mounted read-only, and for the "new" rows the
  worktree's `src/` mounted over `/app/src`. The checkpoint
  `large-v3-turbo.pt` (1,617,941,637 bytes, SHA-256 `aff26ae4…`) was mounted
  read-only at `/opt/docling-models/large-v3-turbo.pt`.
- Files (not copied anywhere; read from the shares):
  - clip: `akten/LuttIn_Polizei/Unterlagen/20sek_Polizei_Wien_Mitte.m4a`, 23.0 s, 0.6 MB
  - lecture: `literatur/Bürgerliches Recht/PSK - Lecture Casts/10 Mehrpersonalität K. 5 - Angenommene Anweisung.mp3`, 405 s (6.8 min), 16 MB
- Harness: the pipeline reader (`DocumentIngestionPipeline.reader_only()._read_document`),
  i.e. the same path a conversion worker takes, timed around the call (model load
  included). "old" = the image's own code with torch pinned to 1 thread (what the
  Docling thread share gave each of the 16 slots of the live run); "new" = this
  branch's code with the media config's device/threads.

## Results

| Run | Code | Device / threads | Wall clock | × real time | Transcript |
|-----|------|------------------|-----------:|------------:|------------|
| clip | old | CPU, 1 thread | 145 s | 0.16 | 278 chars, **English** (hard-coded `language="en"` made Whisper translate the German police recording) |
| clip | new | CPU, 10 threads | 122 s | 0.19 | 485 chars, German |
| clip | new | CUDA | 51 s | 0.45 | 485 chars, German |
| lecture | new | CPU, 10 threads | 1,043 s | 0.39 | 6,744 chars, German |
| lecture | new | CUDA, model loaded per file | 70 s | 5.8 | 6,744 chars |
| lecture | new | CUDA, media worker (model cached), 3 files in one process | 77 s, **26 s, 27 s** | 15.7 / 14.9 | 6,744 chars |

Reading the rows:

- The clip is dominated by fixed costs (about 27 s to load the 1.6 GB checkpoint
  and verify its SHA-256, language detection, the first 30-second window), which is
  why 10 threads barely beat 1 thread on it. The lecture shows the real rates.
- CPU with 10 threads next to a running 16-slot ingestion: 0.4× real time, i.e.
  a 7-minute lecture takes 17 minutes. The old live configuration (1 thread) did
  not finish the lecture within the benchmark window; from the clip ratio it is
  roughly 0.15× real time, 45 minutes for this lecture and days for the corpus.
- GPU: 5.8× real time when the model is loaded for every file, **15× once the
  media worker keeps the model** (23-27 s of decode for 405 s of audio). The
  dedicated media workers (`media.max_concurrent` processes, model cached) exist
  because the per-file load would otherwise cost about 45 s of every 70 s.
- The old code transcribed the German clip into English: `language` was
  hard-coded to `en`. The new default lets Whisper detect the language; `de` can
  be pinned in the media config.

## Corpus estimate

The deferred media of job `b98d150e` (234 records: 160 mp4, 32 mp3, 20 mov,
18 m4a, 2 avi, 2 wmv) are about 5 GB of audio and 30 GB of video; at the lecture
bit rate that is on the order of 50-80 hours of audio plus the videos' tracks. At
15× real time on the GPU with 2 media workers a media run of the recordings is a
matter of hours; on the CPU at 0.4× it is a week of 10-thread work.

## Finding on the live container

`/opt/docling-models/large-v3-turbo.pt` in `rag-backend-quellex` is 893,403,136
bytes: a partial download (the full checkpoint is 1,617,941,637 bytes). openai-whisper
checks the SHA-256 on every load and re-downloads on a mismatch, so every media
file the live job touches starts the 1.6 GB download again. The reader now checks
the hash once per process and records a mismatch as an error
(`whisper_checkpoint_problem`); the rebuilt base image bakes the verified file.

## Reproduce

```bash
# in the worktree; SCR = a scratch folder holding bench_media.py and models/large-v3-turbo.pt
docker run --rm --gpus all \
  -v $SCR/models/large-v3-turbo.pt:/opt/docling-models/large-v3-turbo.pt:ro \
  -v $SCR/bench_media.py:/bench/bench_media.py:ro \
  -v recallhub_share_literatur:/lit:ro -v $PWD/src:/app/src:ro \
  -e MONGODB_URI=mongodb://127.0.0.1:1/ -e MONGODB_DATABASE=bench -e EMBEDDING_API_KEY=dummy -e LLM_API_KEY= \
  --entrypoint /app/.venv/bin/python recallhub-backend:latest \
  /bench/bench_media.py "/lit/<file>.mp3" --mode new --device cuda --cache --repeat 3
```

`bench_media.py` (kept with the session's scratch files, ~60 lines) builds the
reader, calls `_read_document` and prints one JSON line with the timing; `--mode
old` runs the image's own code with torch pinned to `--threads`.
