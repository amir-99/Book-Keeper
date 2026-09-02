import importlib.util
import json
import os
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
PROXY_PATH = REPO_DIR / "letta-openai-proxy.py"


def load_proxy():
    os.environ.setdefault("LETTA_API_KEY", "test-letta-key")
    spec = importlib.util.spec_from_file_location("letta_openai_proxy", PROXY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = load_proxy()


def split(tokens):
    splitter = proxy.ReasoningSplitter()
    segments = []
    for token in tokens:
        segments += splitter.feed(token)
    segments += splitter.flush()
    return segments


def joined(segments, channel):
    return "".join(text for name, text in segments if name == channel)


class ReasoningSplitterTest(unittest.TestCase):
    def test_tag_split_across_tokens_never_reaches_content(self):
        # The token boundaries Letta actually streams for the office-agent preamble.
        segments = split(["<th", "ink", ">I", "’ll", " delegate", ".</", "think", ">\n", "4"])

        self.assertEqual(joined(segments, "reasoning_content"), "I’ll delegate.")
        self.assertEqual(joined(segments, "content"), "4")

    def test_whole_block_in_one_token(self):
        segments = split(["<think>plan</think>answer"])

        self.assertEqual(joined(segments, "reasoning_content"), "plan")
        self.assertEqual(joined(segments, "content"), "answer")

    def test_text_before_the_block_stays_content(self):
        segments = split(["hi ", "<think>", "plan", "</think>", "done"])

        self.assertEqual(joined(segments, "content"), "hi done")
        self.assertEqual(joined(segments, "reasoning_content"), "plan")

    def test_partial_tag_is_held_until_it_is_resolved(self):
        splitter = proxy.ReasoningSplitter()

        self.assertEqual(splitter.feed("<th"), [])
        self.assertEqual(splitter.feed("ing"), [("content", "<thing")])

    def test_unterminated_block_flushes_as_reasoning(self):
        segments = split(["<think>", "still thinking"])

        self.assertEqual(joined(segments, "reasoning_content"), "still thinking")
        self.assertEqual(joined(segments, "content"), "")

    def test_second_block_is_routed_too(self):
        segments = split(["<think>a</think>one<think>b</think>two"])

        self.assertEqual(joined(segments, "reasoning_content"), "ab")
        self.assertEqual(joined(segments, "content"), "onetwo")

    def test_progress_beats_alternate_across_token_boundaries(self):
        # The code-review manager emits one short block before each routing call,
        # so several blocks arrive in one response with the tags split as usual.
        segments = split(
            ["<th", "ink", ">Resolving !42.</thi", "nk", ">",
             "<think>", "Reading the diff.", "</think>",
             "<think>Reviewing.</think>", "Done."]
        )

        self.assertEqual(
            joined(segments, "reasoning_content"),
            "Resolving !42.Reading the diff.Reviewing.",
        )
        self.assertEqual(joined(segments, "content"), "Done.")
        self.assertEqual(
            [name for name, _ in segments],
            ["reasoning_content", "reasoning_content", "reasoning_content", "content"],
        )


def chunk(content=None, finish_reason=None, role=None):
    delta = {"content": content, "function_call": None, "refusal": None, "role": role, "tool_calls": None}
    return {
        "id": "chatcmpl-run-1",
        "object": "chat.completion.chunk",
        "choices": [{"delta": delta, "finish_reason": finish_reason, "index": 0, "logprobs": None}],
    }


class RewriteChunkTest(unittest.TestCase):
    def rewrite(self, chunks):
        splitters = {}
        events = []
        for value in chunks:
            events += proxy.rewrite_chunk(value, splitters)
        return events

    def test_reasoning_goes_out_on_the_reasoning_channel(self):
        events = self.rewrite([chunk("<think>", role="assistant"), chunk("plan"), chunk("</think>"), chunk("hi")])
        deltas = [event["choices"][0]["delta"] for event in events]

        self.assertEqual([delta.get("reasoning_content") for delta in deltas], [None, "plan", None, None])
        self.assertEqual([delta.get("content") for delta in deltas], [None, None, None, "hi"])
        self.assertEqual(deltas[0].get("role"), "assistant")

    def test_one_chunk_closing_the_block_yields_both_channels(self):
        events = self.rewrite([chunk("<think>plan</think>hi")])
        deltas = [event["choices"][0]["delta"] for event in events]

        self.assertEqual(deltas[0].get("reasoning_content"), "plan")
        self.assertEqual(deltas[1].get("content"), "hi")
        self.assertIsNone(deltas[0].get("content"))

    def test_finish_reason_lands_on_the_last_event_only(self):
        events = self.rewrite([chunk("<think>plan</think>hi", finish_reason="stop")])

        self.assertEqual([event["choices"][0]["finish_reason"] for event in events], [None, "stop"])

    def test_repeated_beats_keep_their_channels_across_chunks(self):
        events = self.rewrite(
            [chunk("<think>step one</think>", role="assistant"), chunk("<think>step two</think>"), chunk("answer")]
        )
        deltas = [event["choices"][0]["delta"] for event in events]

        self.assertEqual(
            [d.get("reasoning_content") for d in deltas if d.get("reasoning_content")],
            ["step one", "step two"],
        )
        self.assertEqual([d.get("content") for d in deltas if d.get("content")], ["answer"])

    def test_chunks_without_content_pass_through(self):
        usage_chunk = {"id": "chatcmpl-run-1", "choices": [], "usage": {"total_tokens": 7}}

        self.assertEqual(proxy.rewrite_chunk(usage_chunk, {}), [usage_chunk])

    def test_sse_event_is_a_complete_event_block(self):
        self.assertEqual(proxy.sse_event({"a": 1}), b'data: {"a":1}\n\n')

    def test_serialized_events_stay_valid_json(self):
        events = self.rewrite([chunk("<think>plan</think>hi")])

        for event in events:
            self.assertEqual(json.loads(proxy.sse_event(event)[6:].strip()), event)


if __name__ == "__main__":
    unittest.main()
