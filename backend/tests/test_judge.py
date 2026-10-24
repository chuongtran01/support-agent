from judge import INSTRUCTIONS, JudgeReply, Judgment, build_prompt, judge_answer


class FakeJudge:
    def __init__(self, reply: JudgeReply):
        self.reply = reply
        self.asked: list[tuple[str, str]] = []

    async def judge(self, instructions, prompt):
        self.asked.append((instructions, prompt))
        return self.reply


def reply(supported=True, answers=True, reason="ok"):
    return JudgeReply(Judgment(supported=supported, answers_question=answers, reason=reason), 30, 4)


def test_the_prompt_holds_the_question_the_answer_and_the_passages_and_nothing_else():
    prompt = build_prompt(
        "How long do I have?", "30 days.", ["<source>a</source>", "<source>b</source>"]
    )
    assert "How long do I have?" in prompt and "30 days." in prompt
    assert prompt.index("<source>a</source>") < prompt.index("<source>b</source>")
    # The judge is not told the right answer, so it cannot grade by matching it
    assert "must_include" not in prompt and "expected" not in prompt.lower()


def test_no_passages_is_said_so_rather_than_left_blank():
    assert "retrieved nothing" in build_prompt("q", "a", [])


async def test_a_verdict_carries_the_judges_reasons_and_tokens():
    judge = FakeJudge(reply(supported=False, reason="the 45 days is in no passage"))
    verdict = await judge_answer(judge, "q", "45 days", ["<source>30 days</source>"])
    assert verdict is not None
    assert (verdict.supported, verdict.answers_question) == (False, True)
    assert verdict.reason == "the 45 days is in no passage"
    assert (verdict.prompt_tokens, verdict.completion_tokens) == (30, 4)
    assert judge.asked[0][0] == INSTRUCTIONS


async def test_an_empty_answer_is_not_sent_to_the_judge():
    judge = FakeJudge(reply())
    assert await judge_answer(judge, "q", "   ", []) is None
    assert judge.asked == []


async def test_an_unusable_reply_gives_no_verdict():
    assert await judge_answer(FakeJudge(JudgeReply(None, 30, 4)), "q", "a", []) is None


def test_the_instructions_treat_stale_and_draft_passages_as_no_support():
    assert "stale" in INSTRUCTIONS and "draft" in INSTRUCTIONS
