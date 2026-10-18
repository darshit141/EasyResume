import json
import logging
import re

from django.db import DatabaseError

from resumes.models import JobResumeCuration, Resume
from resumes.prompts import GAP_ANALYSIS_SCHEMA, build_gap_analysis_prompt
from resumes.services.llm_service import (
    LLMServiceError,
    LLMSchemaValidationError,
    get_llm_service,
)


logger = logging.getLogger(__name__)

MATCHED = "MATCHED"
PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
MISSING = "MISSING"
NOT_VERIFIABLE = "NOT_VERIFIABLE"
MATCH_STATUSES = {MATCHED, PARTIALLY_MATCHED, MISSING, NOT_VERIFIABLE}

REQUIREMENT_BUCKETS = [
    ("required_skills", "required_skill"),
    ("preferred_skills", "preferred_skill"),
    ("technologies", "technology"),
    ("frameworks", "framework"),
    ("databases", "database"),
    ("cloud_technologies", "cloud_technology"),
    ("tools", "tool"),
    ("soft_skills", "soft_skill"),
    ("responsibilities", "responsibility"),
    ("experience_requirements", "experience_requirement"),
    ("education_requirements", "education_requirement"),
    ("certification_requirements", "certification_requirement"),
    ("important_keywords", "important_keyword"),
    ("ats_relevant_terms", "ats_relevant_term"),
    ("other_explicit_requirements", "other_explicit_requirement"),
]

EXACT_MATCH_CATEGORIES = {
    "required_skill",
    "preferred_skill",
    "technology",
    "framework",
    "database",
    "cloud_technology",
    "tool",
    "soft_skill",
    "certification_requirement",
    "education_requirement",
    "important_keyword",
    "ats_relevant_term",
}

TECH_ALIASES = {
    "react": ["react", "react js", "react.js", "reactjs"],
    "react.js": ["react", "react js", "react.js", "reactjs"],
    "reactjs": ["react", "react js", "react.js", "reactjs"],
    "node": ["node", "node js", "node.js", "nodejs"],
    "node.js": ["node", "node js", "node.js", "nodejs"],
    "nodejs": ["node", "node js", "node.js", "nodejs"],
    "vue": ["vue", "vue js", "vue.js", "vuejs"],
    "vue.js": ["vue", "vue js", "vue.js", "vuejs"],
    "vuejs": ["vue", "vue js", "vue.js", "vuejs"],
    "angular": ["angular", "angular js", "angular.js", "angularjs"],
    "angularjs": ["angular", "angular js", "angular.js", "angularjs"],
    "k8s": ["k8s", "kubernetes"],
    "kubernetes": ["k8s", "kubernetes"],
    "postgres": ["postgres", "postgresql", "psql"],
    "postgresql": ["postgres", "postgresql", "psql"],
    "psql": ["postgres", "postgresql", "psql"],
    "mongo": ["mongo", "mongodb"],
    "mongodb": ["mongo", "mongodb"],
    "aws": ["aws", "amazon web services", "amazon cloud"],
    "amazon web services": ["aws", "amazon web services", "amazon cloud"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "google cloud": ["gcp", "google cloud", "google cloud platform"],
    "google cloud platform": ["gcp", "google cloud", "google cloud platform"],
    "azure": ["azure", "microsoft azure"],
    "microsoft azure": ["azure", "microsoft azure"],
    "ts": ["ts", "typescript"],
    "typescript": ["ts", "typescript"],
    "js": ["js", "javascript"],
    "javascript": ["js", "javascript"],
    "py": ["py", "python"],
    "python": ["py", "python"],
    "golang": ["golang", "go", "go lang"],
    "go": ["golang", "go", "go lang"],
    "ci cd": ["ci cd", "cicd", "ci/cd", "continuous integration", "continuous deployment", "continuous delivery"],
    "cicd": ["ci cd", "cicd", "ci/cd", "continuous integration", "continuous deployment", "continuous delivery"],
    "ci/cd": ["ci cd", "cicd", "ci/cd", "continuous integration", "continuous deployment", "continuous delivery"],
    "continuous integration": ["ci cd", "cicd", "ci/cd", "continuous integration"],
    "continuous deployment": ["ci cd", "cicd", "ci/cd", "continuous deployment", "continuous delivery"],
    "rest": ["rest", "restful", "rest api", "restful api", "restful apis"],
    "restful": ["rest", "restful", "rest api", "restful api", "restful apis"],
    "rest api": ["rest", "restful", "rest api", "restful api"],
    "restful api": ["rest", "restful", "rest api", "restful api"],
    "graphql": ["graphql", "gql"],
    "gql": ["graphql", "gql"],
    "docker": ["docker", "container", "containers", "containerization"],
    "containers": ["docker", "container", "containers", "containerization"],
    "containerization": ["docker", "container", "containers", "containerization"],
    "ml": ["ml", "machine learning"],
    "machine learning": ["ml", "machine learning"],
    "ai": ["ai", "artificial intelligence", "genai", "gen ai", "generative ai"],
    "artificial intelligence": ["ai", "artificial intelligence", "genai", "generative ai"],
    "dl": ["dl", "deep learning"],
    "deep learning": ["dl", "deep learning"],
    "nlp": ["nlp", "natural language processing"],
    "natural language processing": ["nlp", "natural language processing"],
    "cv": ["cv", "computer vision"],
    "computer vision": ["cv", "computer vision"],
    "sql server": ["sql server", "mssql", "ms sql", "microsoft sql server"],
    "mssql": ["sql server", "mssql", "ms sql", "microsoft sql server"],
    "nosql": ["nosql", "no sql", "non relational"],
    "qa": ["qa", "quality assurance", "software testing", "test automation", "testing"],
    "quality assurance": ["qa", "quality assurance", "software testing", "test automation"],
    "tdd": ["tdd", "test driven development"],
    "html": ["html", "html5"],
    "html5": ["html", "html5"],
    "css": ["css", "css3"],
    "css3": ["css", "css3"],
    "next": ["next", "next js", "next.js", "nextjs"],
    "nextjs": ["next", "next js", "next.js", "nextjs"],
    "next.js": ["next", "next js", "next.js", "nextjs"],
    "express": ["express", "express js", "express.js", "expressjs"],
    "expressjs": ["express", "express js", "express.js", "expressjs"],
    "spring": ["spring", "spring boot", "springboot"],
    "spring boot": ["spring", "spring boot", "springboot"],
    "django": ["django", "django rest framework", "drf"],
    "drf": ["django rest framework", "drf", "django"],
    "fastapi": ["fastapi", "fast api"],
    "flask": ["flask", "flask api"],
    "kafka": ["kafka", "apache kafka"],
    "spark": ["spark", "apache spark", "pyspark"],
    "pyspark": ["pyspark", "spark", "apache spark"],
    "airflow": ["airflow", "apache airflow"],
    "redis": ["redis", "redis cache"],
    "linux": ["linux", "unix", "ubuntu", "debian", "centos", "redhat", "rhel"],
    "git": ["git", "github", "gitlab", "bitbucket"],
    "github": ["git", "github"],
    "gitlab": ["git", "gitlab"],
    "terraform": ["terraform", "iac", "infrastructure as code"],
}


class CandidateDataUnavailableError(LLMServiceError):
    code = "CANDIDATE_DATA_UNAVAILABLE"
    message = "No completed resume data is available for this user."


class JobRequirementsUnavailableError(LLMServiceError):
    code = "JOB_REQUIREMENTS_UNAVAILABLE"
    message = "Structured job requirements are not available for this curation."


class JobGapAnalysisService:
    """
    Builds verified candidate data and compares it to analyzed job requirements.
    """

    def __init__(self, llm_service=None, provider=None, user=None, model=None, fallback_provider=None):
        """``user`` is the account the work is being done for. It is used for
        one thing: if they have saved their own provider API key, the client
        built here uses it instead of the server's (see
        billing.services.user_ai_key_service). Left None — a management
        command, a health check — the server's own credentials are used, which
        is the behaviour this had before BYOK existed."""
        self.user = user
        self.llm_service = llm_service or get_llm_service(
            provider, user=user, model=model, fallback_provider=fallback_provider
        )

    def get_or_create_gap_analysis(self, user, curation_id, regenerate=False):
        curation = JobResumeCuration.objects.get(id=curation_id, user=user)
        if curation.gap_analysis_data and not regenerate:
            return curation

        if curation.status != "completed" or not curation.analysis_data:
            if curation.job_description and curation.job_description.strip():
                from resumes.services.job_curation_service import JobCurationService
                JobCurationService(llm_service=self.llm_service, user=user).run_analysis(curation)
                curation.refresh_from_db()
            else:
                raise JobRequirementsUnavailableError()

        candidate_data = self.build_candidate_data(user, resume_id=curation.resume_id)
        job_requirements = self.build_job_requirements(curation.analysis_data)
        prompt = build_gap_analysis_prompt(
            json.dumps(candidate_data, sort_keys=True),
            json.dumps(job_requirements, sort_keys=True),
        )

        logger.info(
            "Candidate matching started user_id=%s curation_id=%s regenerate=%s",
            user.id,
            curation_id,
            regenerate,
        )
        try:
            raw_analysis = self.llm_service.generate_structured(
                prompt,
                GAP_ANALYSIS_SCHEMA,
            )
            analysis = self.validate_gap_analysis(
                raw_analysis,
                candidate_data,
                job_requirements,
            )
            curation.candidate_data_snapshot = candidate_data
            curation.gap_analysis_data = analysis
            curation.gap_analysis_error_code = ""
            curation.save(
                update_fields=[
                    "candidate_data_snapshot",
                    "gap_analysis_data",
                    "gap_analysis_error_code",
                    "updated_at",
                ]
            )
            logger.info(
                "Candidate matching completed user_id=%s curation_id=%s "
                "match_percentage=%s",
                user.id,
                curation_id,
                analysis.get("overall_match_percentage"),
            )
            return curation
        except LLMServiceError as exc:
            logger.warning(
                "Candidate matching failed user_id=%s curation_id=%s code=%s",
                user.id,
                curation_id,
                exc.code,
            )
            self._mark_failed(curation, exc.code)
            raise
        except DatabaseError as exc:
            logger.exception("Failed to save job gap analysis.")
            raise

    @staticmethod
    def build_candidate_data(user, resume_id=None):
        if resume_id:
            resume = Resume.objects.filter(id=resume_id, user=user, status="completed").first()
        else:
            resume = Resume.objects.filter(
                user=user,
                status="completed",
            ).order_by("-updated_at", "-id").first()
        if not resume or not resume.parsed_data:
            raise CandidateDataUnavailableError()

        return {
            "source": "latest_completed_resume.parsed_data",
            "resume_id": resume.id,
            "original_filename": resume.original_filename,
            "parsed_data": resume.parsed_data,
        }

    @staticmethod
    def build_job_requirements(analysis_data):
        requirements = []
        for field, category in REQUIREMENT_BUCKETS:
            for item in analysis_data.get(field, []):
                if isinstance(item, str) and item.strip():
                    requirements.append({
                        "requirement": item.strip(),
                        "category": category,
                    })

        seniority = analysis_data.get("seniority_level")
        if isinstance(seniority, str) and seniority.strip():
            requirements.append({
                "requirement": seniority.strip(),
                "category": "seniority_level",
            })

        if not requirements:
            raise JobRequirementsUnavailableError()

        return requirements

    def validate_gap_analysis(self, analysis, candidate_data, job_requirements):
        if not isinstance(analysis, dict):
            raise LLMSchemaValidationError()

        required_keys = set(GAP_ANALYSIS_SCHEMA["required"])
        if set(analysis) != required_keys:
            raise LLMSchemaValidationError()

        candidate_terms = self._candidate_terms(candidate_data)
        candidate_text = self._normalise_text(json.dumps(candidate_data, sort_keys=True))
        requirements_by_key = {
            self._requirement_key(item["requirement"], item["category"]): item
            for item in job_requirements
        }

        validated_items = []
        for bucket in [
            "matched_requirements",
            "partially_matched_requirements",
            "missing_requirements",
            "not_verifiable_requirements",
        ]:
            if not isinstance(analysis.get(bucket), list):
                raise LLMSchemaValidationError()
            for item in analysis[bucket]:
                validated_items.append(
                    self._validate_requirement_item(
                        item,
                        requirements_by_key,
                        candidate_terms,
                        candidate_text,
                        candidate_data=candidate_data,
                    )
                )

        seen_keys = {
            self._requirement_key(item["requirement"], item["category"])
            for item in validated_items
        }
        for requirement in job_requirements:
            key = self._requirement_key(
                requirement["requirement"],
                requirement["category"],
            )
            if key not in seen_keys:
                validated_items.append({
                    **requirement,
                    "match_status": MISSING,
                    "candidate_evidence": [],
                    "confidence": 1,
                    "reasoning": "No candidate evidence was provided for this requirement.",
                    "recommendation": "Acquire this requirement or add it only if it is genuinely part of your experience.",
                })

        result = {
            **analysis,
            "matched_requirements": [],
            "partially_matched_requirements": [],
            "missing_requirements": [],
            "not_verifiable_requirements": [],
        }
        for item in validated_items:
            if item["match_status"] == MATCHED:
                result["matched_requirements"].append(item)
            elif item["match_status"] == PARTIALLY_MATCHED:
                result["partially_matched_requirements"].append(item)
            elif item["match_status"] == NOT_VERIFIABLE:
                result["not_verifiable_requirements"].append(item)
            else:
                result["missing_requirements"].append(item)

        total = len(validated_items)
        score = sum(
            1 if item["match_status"] == MATCHED else 0.5 if item["match_status"] == PARTIALLY_MATCHED else 0
            for item in validated_items
        )
        result["overall_match_percentage"] = round((score / total) * 100, 2) if total else 0
        self._validate_summary_fields(result)
        self._recompute_ats_coverage(result, candidate_terms, candidate_text)
        return result

    @classmethod
    def _recompute_ats_coverage(cls, result, candidate_terms=None, candidate_text=""):
        """Derive the ATS coverage percentage from the keyword lists and verify
        them against actual candidate data instead of trusting raw model hallucination.
        """
        coverage = result.get("ats_keyword_coverage") or {}
        raw_covered = coverage.get("covered_keywords") or []
        raw_missing = coverage.get("missing_keywords") or []

        if candidate_terms is None and not candidate_text:
            verified_covered = [kw for kw in raw_covered if isinstance(kw, str) and kw.strip()]
            verified_missing = [kw for kw in raw_missing if isinstance(kw, str) and kw.strip()]
        else:
            all_keywords = []
            for kw in raw_covered + raw_missing:
                if isinstance(kw, str) and kw.strip() and kw.strip() not in all_keywords:
                    all_keywords.append(kw.strip())

            verified_covered = []
            verified_missing = []

            for kw in all_keywords:
                norm = cls._normalise_text(kw)
                if cls._is_term_in_candidate(norm, candidate_terms, candidate_text):
                    verified_covered.append(kw)
                else:
                    verified_missing.append(kw)

        total = len(verified_covered) + len(verified_missing)
        coverage["covered_keywords"] = sorted(verified_covered, key=lambda s: s.lower())
        coverage["missing_keywords"] = sorted(verified_missing, key=lambda s: s.lower())
        coverage["coverage_percentage"] = (
            round((len(verified_covered) / total) * 100, 2) if total else 0
        )
        result["ats_keyword_coverage"] = coverage

    def _validate_requirement_item(
        self,
        item,
        requirements_by_key,
        candidate_terms,
        candidate_text,
        candidate_data=None,
    ):
        if not isinstance(item, dict):
            raise LLMSchemaValidationError()

        expected = {
            "requirement", "category", "match_status", "candidate_evidence",
            "confidence", "reasoning", "recommendation",
        }
        if set(item) != expected:
            raise LLMSchemaValidationError()
        if item["match_status"] not in MATCH_STATUSES:
            raise LLMSchemaValidationError()
        if not isinstance(item["candidate_evidence"], list):
            raise LLMSchemaValidationError()
        if not isinstance(item["confidence"], (int, float)):
            raise LLMSchemaValidationError()

        requirement_key = self._requirement_key(item["requirement"], item["category"])
        if requirement_key not in requirements_by_key:
            raise LLMSchemaValidationError()

        requirement_norm = self._normalise_text(item["requirement"])
        evidence_is_supported = self._evidence_is_supported(
            item["candidate_evidence"],
            candidate_text,
            requirement_norm=requirement_norm,
            candidate_terms=candidate_terms,
        )

        if item["category"] == "experience_requirement":
            return self._enforce_experience_match(item, candidate_text, candidate_data=candidate_data)

        if item["category"] in EXACT_MATCH_CATEGORIES:
            has_term = self._is_term_in_candidate(requirement_norm, candidate_terms, candidate_text)
            if item["match_status"] == MATCHED:
                if not has_term:
                    if self._has_partial_overlap(requirement_norm, candidate_terms, candidate_text):
                        item = {
                            **item,
                            "match_status": PARTIALLY_MATCHED,
                            "reasoning": f"Partial candidate evidence was found in resume data for {item['requirement']}.",
                        }
                    else:
                        return self._downgrade_to_missing(
                            item,
                            "This requirement was not explicitly present in verified candidate data.",
                        )
            elif item["match_status"] == PARTIALLY_MATCHED:
                if not has_term and not self._has_partial_overlap(requirement_norm, candidate_terms, candidate_text):
                    return self._downgrade_to_missing(
                        item,
                        "No supported partial candidate evidence was found.",
                    )

        if item["match_status"] in {MATCHED, PARTIALLY_MATCHED} and not evidence_is_supported:
            return self._downgrade_to_missing(item, "Candidate evidence was not found in verified resume data.")

        # Ensure that if match_status is MATCHED but evidence was omitted by LLM, we populate evidence from candidate_text
        if item["match_status"] == MATCHED and not item["candidate_evidence"]:
            snippet = self._extract_evidence_snippet(requirement_norm, candidate_text)
            if snippet:
                item["candidate_evidence"] = [snippet]

        return {
            **item,
            "confidence": max(0, min(float(item["confidence"]), 1)),
        }

    def _enforce_experience_match(self, item, candidate_text, candidate_data=None):
        required_years = self._max_years(item["requirement"])
        candidate_years = self._candidate_experience_years(candidate_text, candidate_data)
        if required_years is None:
            return item
        if candidate_years is None:
            return self._downgrade_to_missing(
                item,
                "Candidate years of experience are not present in verified resume data.",
            )
        if candidate_years >= required_years:
            return {**item, "match_status": MATCHED, "confidence": max(0, min(float(item["confidence"]), 1))}
        return {
            **item,
            "match_status": PARTIALLY_MATCHED,
            "confidence": max(0, min(float(item["confidence"]), 1)),
            "reasoning": f"Candidate data indicates {candidate_years:g} years against a {required_years:g}-year requirement.",
        }

    @classmethod
    def _candidate_experience_years(cls, candidate_text, candidate_data=None):
        max_explicit = cls._max_years(candidate_text)
        
        parsed = (candidate_data.get("parsed_data") if isinstance(candidate_data, dict) else None) or candidate_data or {}
        experience_entries = parsed.get("experience", []) if isinstance(parsed, dict) else []
        
        import datetime
        current_year = datetime.datetime.now().year
        
        year_ranges = []
        for entry in experience_entries:
            text = ""
            if isinstance(entry, dict):
                text = f"{entry.get('header', '')} {' '.join(entry.get('bullets', []))}"
            elif isinstance(entry, str):
                text = entry
            
            matches = re.findall(r"\b(19\d\d|20\d\d)\b\s*(?:-|–|—|to)\s*\b(19\d\d|20\d\d|present|current|now)\b", text, re.IGNORECASE)
            for start_str, end_str in matches:
                try:
                    s_yr = int(start_str)
                    e_yr = current_year if end_str.lower() in {"present", "current", "now"} else int(end_str)
                    if 1970 <= s_yr <= current_year and e_yr >= s_yr:
                        year_ranges.append(e_yr - s_yr)
                except (ValueError, TypeError):
                    continue

        calculated_years = sum(year_ranges) if year_ranges else None
        candidates = [y for y in [max_explicit, calculated_years] if y is not None]
        return max(candidates) if candidates else None

    @staticmethod
    def _downgrade_to_missing(item, reason):
        return {
            **item,
            "match_status": MISSING,
            "candidate_evidence": [],
            "confidence": 1,
            "reasoning": reason,
            "recommendation": "Do not add this to the resume unless it is genuinely part of the candidate's experience.",
        }

    @staticmethod
    def _validate_summary_fields(result):
        for field in [
            "strengths",
            "weaknesses_gaps",
            "important_missing_information",
            "interview_readiness_observations",
        ]:
            if not isinstance(result.get(field), list):
                raise LLMSchemaValidationError()
        recommendations = result.get("recommendations_for_profile_improvement")
        if not isinstance(recommendations, list):
            raise LLMSchemaValidationError()
        for item in recommendations:
            if not isinstance(item, dict):
                raise LLMSchemaValidationError()
            if set(item) != {"recommendation", "recommendation_type"}:
                raise LLMSchemaValidationError()
            if item["recommendation_type"] not in {"MISSING_PROFILE_INFORMATION", "SKILL_TO_ACQUIRE"}:
                raise LLMSchemaValidationError()
        coverage = result.get("ats_keyword_coverage")
        if not isinstance(coverage, dict):
            raise LLMSchemaValidationError()
        if set(coverage) != {"covered_keywords", "missing_keywords", "coverage_percentage"}:
            raise LLMSchemaValidationError()

    @classmethod
    def _candidate_terms(cls, candidate_data):
        terms = set()

        def extract_tokens(text):
            if not text or not isinstance(text, str):
                return
            norm = cls._normalise_text(text)
            if norm:
                terms.add(norm)

            # Split by delimiters (commas, slashes, brackets, bullets, dashes)
            parts = re.split(r'[,/;|\n\r•\(\)\-\[\]]+', text)
            for part in parts:
                p_norm = cls._normalise_text(part)
                if p_norm and len(p_norm) >= 2:
                    terms.add(p_norm)

            # N-grams (1-gram to 3-gram) from normalized words
            words = norm.split()
            for w in words:
                if len(w) >= 2:
                    terms.add(w)
            for i in range(len(words) - 1):
                terms.add(f"{words[i]} {words[i+1]}")
            for i in range(len(words) - 2):
                terms.add(f"{words[i]} {words[i+1]} {words[i+2]}")

        def collect(value):
            if isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
            elif isinstance(value, str):
                extract_tokens(value)

        target = candidate_data.get("parsed_data") if isinstance(candidate_data, dict) else None
        collect(target or candidate_data)
        return terms

    @classmethod
    def _get_aliases(cls, term):
        norm = cls._normalise_text(term)
        variants = {norm}
        if norm in TECH_ALIASES:
            for alias in TECH_ALIASES[norm]:
                variants.add(cls._normalise_text(alias))
        return variants

    @classmethod
    def _is_term_in_candidate(cls, requirement_norm, candidate_terms, candidate_text):
        if not requirement_norm:
            return False
        aliases = cls._get_aliases(requirement_norm)
        for alias in aliases:
            if alias in candidate_terms:
                return True
            pattern = r"(?:^|\s)" + re.escape(alias) + r"(?:\s|$)"
            if re.search(pattern, candidate_text):
                return True
        return False

    @classmethod
    def _has_partial_overlap(cls, requirement_norm, candidate_terms, candidate_text):
        aliases = cls._get_aliases(requirement_norm)
        stopwords = {"and", "or", "in", "with", "of", "for", "to", "a", "an", "the", "on", "at", "by", "as"}
        for alias in aliases:
            tokens = set(alias.split()) - stopwords
            for token in tokens:
                if len(token) >= 3 and cls._is_term_in_candidate(token, candidate_terms, candidate_text):
                    return True
        return False

    @classmethod
    def _evidence_is_supported(cls, evidence, candidate_text, requirement_norm=None, candidate_terms=None):
        if requirement_norm and candidate_terms and cls._is_term_in_candidate(requirement_norm, candidate_terms, candidate_text):
            return True
        if not evidence:
            return False
        for item in evidence:
            if not isinstance(item, str):
                raise LLMSchemaValidationError()
            normalised = cls._normalise_text(item)
            if not normalised:
                continue
            if normalised in candidate_text:
                return True
            pattern = r"(?:^|\s)" + re.escape(normalised) + r"(?:\s|$)"
            if re.search(pattern, candidate_text):
                return True
            tokens = [t for t in normalised.split() if len(t) >= 3 and t not in {
                "and", "the", "with", "for", "from", "that", "this", "have", "been", "using", "work", "role"
            }]
            if len(tokens) >= 2:
                matched_tokens = sum(1 for t in tokens if re.search(r"(?:^|\s)" + re.escape(t) + r"(?:\s|$)", candidate_text))
                if matched_tokens / len(tokens) >= 0.5:
                    return True
        return False

    @classmethod
    def _extract_evidence_snippet(cls, requirement_norm, candidate_text):
        aliases = cls._get_aliases(requirement_norm)
        for alias in aliases:
            pattern = r"(?:^|\s)(" + re.escape(alias) + r")(?:\s|$)"
            match = re.search(pattern, candidate_text)
            if match:
                start = max(0, match.start() - 30)
                end = min(len(candidate_text), match.end() + 30)
                return candidate_text[start:end].strip()
        return ""

    @staticmethod
    def _max_years(text):
        matches = re.findall(r"(\d+(?:\.\d+)?)\+?\s*(?:years|year|yrs|yr)", text, flags=re.IGNORECASE)
        if not matches:
            return None
        return max(float(match) for match in matches)

    @staticmethod
    def _normalise_text(value):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.+#]+", " ", str(value).lower())).strip()

    @staticmethod
    def _requirement_key(requirement, category):
        return f"{JobGapAnalysisService._normalise_text(category)}::{JobGapAnalysisService._normalise_text(requirement)}"

    @staticmethod
    def _mark_failed(curation, error_code):
        try:
            curation.gap_analysis_error_code = error_code
            curation.save(update_fields=["gap_analysis_error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist job gap analysis failure state.")
