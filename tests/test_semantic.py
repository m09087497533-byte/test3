from unittest import TestCase
from unittest.mock import patch, Mock
from types import SimpleNamespace
from importlib.machinery import ModuleSpec
from studio import semantic
from studio.auto_media import relevant_candidate, rank_candidate


class SemanticTests(TestCase):
    def context(self, **extra):
        return dict(query='astronaut weightlessness',concept='Космос',basis='Космонавты живут в невесомости.',
                    words={'astronaut','weightlessness'},**extra)

    def test_cross_language_paraphrase_can_pass_without_word_overlap(self):
        with patch.object(semantic,'similarity',return_value=.82):
            self.assertTrue(relevant_candidate(dict(title='Crew floating aboard the ISS'),self.context()))

    def test_unrelated_metadata_does_not_pass(self):
        with patch.object(semantic,'similarity',return_value=.1):
            self.assertFalse(relevant_candidate(dict(title='A bowl of apples'),self.context()))

    def test_semantics_cannot_override_explicit_country(self):
        c=self.context();c['query']='Ethiopian manuscript'
        with patch.object(semantic,'similarity',return_value=.99):
            self.assertFalse(relevant_candidate(dict(title='Greek manuscript'),c))

    def test_semantics_cannot_turn_office_notebook_into_old_document(self):
        c=self.context();c['query']='ancient manuscript'
        with patch.object(semantic,'similarity',return_value=.99):
            self.assertFalse(relevant_candidate(dict(title='manuscript notebook next to a laptop'),c))

    def test_meaning_changes_ranking_with_identical_lexical_overlap(self):
        with patch.object(semantic,'similarity',side_effect=[.8,.1]):
            good=rank_candidate(dict(title='Floating crew'),self.context(),8)
            bad=rank_candidate(dict(title='A fruit bowl'),self.context(),8)
        self.assertGreater(good,bad+5)

    def test_absent_model_does_not_claim_similarity(self):
        with patch.object(semantic,'_model',None):
            self.assertIsNone(semantic.similarity('космос','space'))

    def test_missing_model_is_reported_without_breaking_app(self):
        module=SimpleNamespace(__spec__=ModuleSpec('fastembed',loader=None),TextEmbedding=Mock(side_effect=OSError('network')))
        with patch.object(semantic,'_model',None),patch.dict('sys.modules',{'fastembed':module}):
            state=semantic.prepare()
        self.assertFalse(state['ready']);self.assertIn('недоступна',state['message'])

    def test_enabled_project_prepares_model_before_arranging(self):
        from studio.auto_media import arrange
        from studio.models import Project,Scene
        updates=[]
        with patch.object(semantic,'status',return_value=dict(available=True,ready=False)),patch.object(semantic,'prepare',return_value=dict(ready=True)) as prepare:
            arrange(Project(scenes=[Scene()],semantic_media=True),'mixed',False,updates.append,lambda:False,scene_indices=[])
        prepare.assert_called_once()
        self.assertTrue(any(s.get('semantic',{}).get('ready') for s in updates))

    def test_legacy_project_does_not_require_model_download(self):
        from studio.auto_media import arrange
        from studio.models import Project,Scene
        with patch.object(semantic,'prepare') as prepare:
            arrange(Project(scenes=[Scene()]),'mixed',False,lambda _:None,lambda:False,scene_indices=[])
        prepare.assert_not_called()
