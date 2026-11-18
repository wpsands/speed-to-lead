# Speed-to-Lead Test

[![CI](https://github.com/wpsands/speed-to-lead/actions/workflows/ci.yml/badge.svg)](https://github.com/wpsands/speed-to-lead/actions/workflows/ci.yml)

**When a buyer asks for a demo, how fast and how well does the company respond?**

Speed-to-Lead Test is a Claude Code plugin that measures it. You submit one genuine demo request to each company under your real name. The agent logs every email, call, voicemail, text and LinkedIn message for seven days, then scores each company out of 100 and writes a scorecard per company and a benchmark across the group.

See a full report on synthetic data: [examples/sample-run/report.md](examples/sample-run/report.md). All eight companies in it are fictional.

## What a report contains

- A benchmark: median time to first human touch, how many companies replied within 5 minutes and within an hour, how many never replied, how many offered a calendar on submit, median touches and median score.
- A ranking table with score, grade, first human touch, touches and channels.
- A scorecard per company: request time, first human touch and its channel, time to the automated confirmation, touch count, best message, meeting path, form fields, and points by part.

## How it scores

| Part | Max | Measures |
|---|---|---|
| Speed | 40 | Time from request to first human touch |
| Persistence | 20 | Human touches in the window |
| Channels | 10 | Email, phone, SMS and LinkedIn used |
| Personalization | 15 | Whether any reply answered the question asked |
| Meeting | 15 | Shortest path offered to a conversation |

Automated confirmations are logged and reported but never count as a response. Full bands, definitions and the rules for a fair test are in [method/rubric.md](method/rubric.md).

## Install

```
/plugin marketplace add wpsands/speed-to-lead
/plugin install speed-to-lead@speed-to-lead
```

Then ask Claude Code: "Run a speed to lead test on these companies" with a list of names and demo form URLs.

## What it needs

| Source | Used for | Setup |
|---|---|---|
| Your work inbox | Email touches | A Gmail connector signed in as the address you put on forms. Read only. |
| A browser tool | Filling forms | Claude in Chrome or another browser tool. You click submit yourself. |
| Twilio (optional) | Calls, voicemails and texts | A number with voice and SMS, and a voicemail TwiML Bin (below). Without it, phone touches are logged by hand. |
| You | LinkedIn messages | LinkedIn has no API for this; the agent asks and logs what you report. |

### Twilio voicemail

Point the number's incoming call webhook at a TwiML Bin like this, so every call is answered by voicemail and recorded:

```xml
<Response>
  <Say>You have reached Page Sands. Please leave a message.</Say>
  <Record maxLength="120" playBeep="true"/>
</Response>
```

Set three environment variables through your secret manager, not a committed file:

```
TWILIO_ACCOUNT_SID   TWILIO_AUTH_TOKEN   TWILIO_NUMBER   (E.164, for example +14155550100)
```

The script only reads from Twilio. It never places calls or sends texts.

## The CLI

The skill drives `scripts/stl.py`, which you can also run directly. Python 3.10 or later, standard library only.

```
python3 scripts/stl.py init runs/wave-1 --name "Wave 1"
python3 scripts/stl.py add-company runs/wave-1 --name "Acme" --domain acme.com --form-url https://acme.com/demo
python3 scripts/stl.py submit runs/wave-1 --company acme --fields 6 --phone-required
python3 scripts/stl.py import runs/wave-1 inbox-touches.json
python3 scripts/stl.py twilio-pull runs/wave-1
python3 scripts/stl.py status runs/wave-1
python3 scripts/stl.py report runs/wave-1
python3 scripts/stl.py sample /tmp/sample
```

Run folders hold real company data. `runs/` is in `.gitignore`.

## Rules for a fair test

Use your real identity and a question you want answered. One request per company. Do not reply during the window. When it closes, answer every person who reached out. Publish results in aggregate unless a company agrees to be named. Details in [method/rubric.md](method/rubric.md).

## Development

```
python3 -m unittest discover -s tests -v
```

CI runs the tests on Python 3.10, 3.12 and 3.13 and checks that `examples/sample-run/report.md` matches what the code generates.

## About

Built by [Page Sands](https://sandsdx.com/about/) at SandsDX, one of the [GTM agents and skills](https://sandsdx.com/agents/) for building B2B SaaS pipeline. MIT licensed.
