"""The report CLI's exits: what a scheduled invocation or an operator's shell
sees, as distinct from what the renderer prints (covered under service/onchain).
"""
from src.job.onchain import report as job
from src.service.onchain.registry import parse_registry, upsert_registry


def _seed(onchain_repository, onchain_registry_payload):
    project_ids = upsert_registry(onchain_repository, parse_registry(onchain_registry_payload))
    onchain_repository.commit()
    return project_ids


class TestNotFound:
    def test_an_unknown_project_is_one_stderr_line_and_exit_3(
        self, monkeypatch, capsys, onchain_repository, onchain_registry_payload, onchain_database_url
    ):
        _seed(onchain_repository, onchain_registry_payload)
        monkeypatch.setattr(job, 'get_onchain_database_url', lambda mode: onchain_database_url)
        assert job.main(project='nope', test_mode=True) == job.EXIT_NOT_FOUND
        captured = capsys.readouterr()
        assert captured.out == ''
        assert "no project entity for 'nope'" in captured.err

    def test_a_build_id_from_another_project_is_not_rendered_under_this_name(
        self, monkeypatch, capsys, onchain_repository, onchain_registry_payload, onchain_database_url
    ):
        project_ids = _seed(onchain_repository, onchain_registry_payload)
        chain = onchain_repository.get_entity_by_key('project:touch-grass')['parent_id']
        other = onchain_repository.upsert_entity(
            level='project', key='project:other', display_name='Other', parent_id=chain
        )
        run_id = onchain_repository.start_run('onchain.build')
        foreign = onchain_repository.start_build(
            run_id=run_id, project_id=other, block=950, block_timestamp=3
        )
        onchain_repository.finish_build(foreign, outcome='ok')
        onchain_repository.commit()
        assert project_ids['touch-grass'] != other
        monkeypatch.setattr(job, 'get_onchain_database_url', lambda mode: onchain_database_url)

        assert job.main(project='touch-grass', build=foreign, test_mode=True) == job.EXIT_NOT_FOUND
        assert 'Other' not in capsys.readouterr().out
