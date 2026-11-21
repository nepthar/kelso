from kelso.cli.main import run


def _topics(capsys) -> list[str]:
  assert run(["agent-help"]) == 0
  listing = capsys.readouterr().out.partition("## Topics")[2]
  return [line.split()[2] for line in listing.splitlines() if line.strip()]


def test_the_index_lists_every_topic_and_each_one_prints(capsys):
  topics = _topics(capsys)
  assert {"manifest", "upgrade", "write-app"} <= set(topics)
  for topic in topics:
    assert run(["agent-help", topic]) == 0
    assert capsys.readouterr().out.startswith("# ")


def test_an_unknown_topic_names_the_command_that_lists_them(capsys):
  assert run(["agent-help", "nope"]) == 1
  assert "`kelso agent-help` lists them" in capsys.readouterr().err


def test_help_points_an_agent_at_agent_help(capsys):
  assert run(["--help"]) == 0
  assert "kelso agent-help" in capsys.readouterr().out
