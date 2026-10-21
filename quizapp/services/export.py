"""CSV export helpers."""

from __future__ import annotations

import csv
import io
from datetime import timezone

from ..models import ATTEMPT_GRADED, Attempt, Quiz


def results_csv(quiz: Quiz, attempts: list[Attempt]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    header = ["Participant", "Email", "Status", "Submitted (UTC)", "Score", "Max score", "Percent", "Passed"]
    questions = list(quiz.questions)
    for index, question in enumerate(questions, start=1):
        header.append(f"Q{index}: {question.prompt[:80]}")
    writer.writerow(header)

    for attempt in attempts:
        submitted = ""
        if attempt.submitted_at:
            submitted = attempt.submitted_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M")
        row = [
            attempt.user.name,
            attempt.user.email,
            attempt.status_label,
            submitted,
            attempt.score,
            attempt.max_score,
            f"{attempt.percentage}%",
            "Yes" if attempt.percentage >= quiz.pass_percent else "No",
        ]
        for question in questions:
            answer = attempt.answer_for(question.id)
            if answer is None:
                row.append("")
            elif question.type == "written":
                row.append("" if answer.manual_points is None else answer.manual_points)
            else:
                row.append(answer.final_points)
        writer.writerow(row)

    # BOM keeps Excel happy with non-ASCII names.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
