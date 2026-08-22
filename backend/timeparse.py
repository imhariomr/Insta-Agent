"""Parses a user-entered start timestamp — either plain seconds or
MM:SS / H:MM:SS — into seconds, so the batch-creation form can accept
"01:42" like the spec's mockup."""


def parse_timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value:
        return 0.0
    seconds = 0.0
    for part in value.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


if __name__ == "__main__":
    assert parse_timestamp("01:42") == 102.0
    assert parse_timestamp("1:02:03") == 3723.0
    assert parse_timestamp(90) == 90.0
    assert parse_timestamp(90.5) == 90.5
    print("timeparse.py self-check OK")
