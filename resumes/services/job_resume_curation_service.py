import logging

from django.db import DatabaseError, transaction

from resumes.models import JobResumeCuration, JobResumeCurationVersion, Resume
from resumes.resume_content import flatten_parsed_data_for_grounding
from resumes.services.job_curation_service import JobCurationDatabaseError
from resumes.services.job_gap_analysis_service import (
    CandidateDataUnavailableError,
    JobGapAnalysisService,
    JobRequirementsUnavailableError,
)
from resumes.services.llm_service import LLMServiceError
from resumes.services.resume_curation_ai_service import ResumeCurationAIService
from resumes.services.resume_template_service import ResumeTemplateService


logger = logging.getLogger(__name__)


class JobResumeCurationService:
    """
    Coordinates verified candidate source data, structured job requirements,
    and candidate-to-job matching results into a validated, source-grounded,
    job-tailored resume for a curation session.
    """

    def __init__(self, ai_service=None, gap_analysis_service=None, provider=None, user=None, model=None, fallback_provider=None):
        self.user = user
        self.ai_service = ai_service or ResumeCurationAIService(
            provider=provider, user=user, model=model, fallback_provider=fallback_provider
        )
        self.gap_analysis_service = gap_analysis_service or JobGapAnalysisService(
            provider=provider, user=user, model=model, fallback_provider=fallback_provider
        )

    def generate(self, user, curation_id, regenerate=False, add_missing_skills=None, mode=None):
        """Returns (curation, version). ``version`` is the JobResumeCurationVersion
        backing the returned tailored_resume_data, or None if none exists yet."""
        curation = JobResumeCuration.objects.get(id=curation_id, user=user)

        from django.conf import settings
        from api.utils.config import get_config_value
        from resumes.prompts import (
            TAILOR_MODE_SYSTEM_BASELINE,
            TAILOR_MODE_REPHRASE_ONLY,
            TAILOR_MODE_FULL_TAILORED,
            TAILOR_MODES,
        )

        if not mode:
            mode = get_config_value(
                "TAILOR_DEFAULT_MODE",
                getattr(settings, "TAILOR_DEFAULT_MODE", TAILOR_MODE_FULL_TAILORED),
            )
        if mode not in TAILOR_MODES:
            mode = TAILOR_MODE_FULL_TAILORED

        if curation.tailored_resume_data and not regenerate:
            return curation, self._latest_version(curation)

        if curation.status != "completed" or not curation.analysis_data:
            if curation.job_description and curation.job_description.strip():
                from resumes.services.job_curation_service import JobCurationService
                JobCurationService(llm_service=self.ai_service.llm_service, user=user).run_analysis(curation)
                curation.refresh_from_db()
            else:
                raise JobRequirementsUnavailableError()

        # Re-run candidate-vs-job matching against the latest profile before
        # generating, so a stale gap analysis never drives regeneration.
        gap_curation = self.gap_analysis_service.get_or_create_gap_analysis(
            user,
            curation_id,
            regenerate=regenerate,
        )
        candidate_source_data = self.build_candidate_source_data(user, resume_id=curation.resume_id)
        job_requirements = JobGapAnalysisService.build_job_requirements(
            curation.analysis_data
        )

        if add_missing_skills is None:
            add_missing_skills = bool(get_config_value(
                "TAILOR_ADD_MISSING_SKILLS_DEFAULT",
                getattr(settings, "TAILOR_ADD_MISSING_SKILLS_DEFAULT", False)
            ))

        allowed_missing_skills = []
        if add_missing_skills and gap_curation.gap_analysis_data:
            missing_reqs = gap_curation.gap_analysis_data.get("missing_requirements", [])
            for item in missing_reqs:
                cat = item.get("category", "")
                if cat in {
                    "required_skill", "preferred_skill", "technology", "framework",
                    "database", "cloud_technology", "tool", "soft_skill",
                    "important_keyword", "ats_relevant_term"
                }:
                    req = item.get("requirement", "").strip()
                    if req and req not in allowed_missing_skills:
                        allowed_missing_skills.append(req)
            for kw in gap_curation.gap_analysis_data.get("ats_keyword_coverage", {}).get("missing_keywords", []):
                if isinstance(kw, str) and kw.strip() and kw.strip() not in allowed_missing_skills:
                    allowed_missing_skills.append(kw.strip())

        action = f"Resume generation [{mode}]" if not regenerate else f"Resume regeneration [{mode}]"
        logger.info(
            "%s started user_id=%s curation_id=%s mode=%s add_missing_skills=%s (allowed=%s)",
            action, user.id, curation_id, mode, add_missing_skills, len(allowed_missing_skills)
        )
        try:
            if mode == TAILOR_MODE_SYSTEM_BASELINE:
                tailored = self.build_system_baseline_resume(
                    candidate_source_data=candidate_source_data,
                    curation=curation,
                    allowed_missing_skills=allowed_missing_skills,
                )
            elif mode == TAILOR_MODE_REPHRASE_ONLY:
                tailored = self.ai_service.rephrase_resume(
                    candidate_source_data=candidate_source_data,
                )
            else:  # TAILOR_MODE_FULL_TAILORED
                tailored = self.ai_service.generate_tailored_resume(
                    candidate_source_data=candidate_source_data,
                    job_title=curation.job_title,
                    job_description=curation.job_description,
                    job_requirements=job_requirements,
                    candidate_match_summary=gap_curation.gap_analysis_data,
                    allowed_missing_skills=allowed_missing_skills,
                )
        except LLMServiceError as exc:
            # Never touch tailored_resume_data here: the previously persisted
            # tailored resume and its version history must survive a failed
            # regeneration attempt untouched.
            logger.warning(
                "%s failed user_id=%s curation_id=%s code=%s",
                action,
                user.id,
                curation_id,
                exc.code,
            )
            self._mark_failed(curation, exc.code)
            raise

        tailored_resume_data = {
            "personal_information": candidate_source_data["personal_information"],
            "tailoring_mode": mode,
            **tailored,
        }

        try:
            with transaction.atomic():
                curation.tailored_resume_data = tailored_resume_data
                curation.tailored_resume_error_code = ""
                curation.save(
                    update_fields=[
                        "tailored_resume_data",
                        "tailored_resume_error_code",
                        "updated_at",
                    ]
                )
                version = JobResumeCurationVersion.objects.create(
                    curation=curation,
                    version=self._next_version_number(curation),
                    tailored_resume_data=tailored_resume_data,
                    match_percentage=gap_curation.gap_analysis_data.get(
                        "overall_match_percentage"
                    ),
                    gap_analysis_snapshot=gap_curation.gap_analysis_data,
                    # Freeze the layout template in force right now onto this
                    # version, so it always re-renders as it looked when
                    # generated even if the user changes their default later.
                    template_config=ResumeTemplateService.config_for_user(user),
                )
            logger.info(
                "%s completed user_id=%s curation_id=%s version=%s",
                action,
                user.id,
                curation_id,
                version.version,
            )
            return curation, version
        except DatabaseError as exc:
            logger.exception("Failed to save job-tailored resume data.")
            raise JobCurationDatabaseError() from exc

    @staticmethod
    def _latest_version(curation):
        return curation.tailored_resume_versions.order_by("-version").first()

    @classmethod
    def _next_version_number(cls, curation):
        latest = cls._latest_version(curation)
        return 1 if latest is None else latest.version + 1

    @staticmethod
    def build_candidate_source_data(user, resume_id=None):
        if resume_id:
            resume = Resume.objects.filter(id=resume_id, user=user, status="completed").first()
        else:
            resume = Resume.objects.filter(
                user=user,
                status="completed",
            ).order_by("-updated_at", "-id").first()
        if not resume or not resume.parsed_data:
            raise CandidateDataUnavailableError()

        # Flatten every header/bullets entry to a text block so the tailoring
        # prompt and its grounding check — both of which reason over strings
        # — see the whole entry (header AND every bullet), not just a
        # summary line. Legacy string entries pass through unchanged.
        parsed_data = flatten_parsed_data_for_grounding(resume.parsed_data)
        return {
            "resume_id": resume.id,
            "raw_sections": {
                sec: resume.parsed_data.get(sec, [])
                for sec in ("experience", "projects", "education", "certifications")
            },
            "personal_information": parsed_data.get("personal_information") or {
                "name": parsed_data.get("name"),
                "email": parsed_data.get("email"),
                "phone": parsed_data.get("phone"),
            },
            "summary": parsed_data.get("summary"),
            "skills": parsed_data.get("skills", []),
            "experience": parsed_data.get("experience", []),
            "projects": parsed_data.get("projects", []),
            "education": parsed_data.get("education", []),
            "certifications": parsed_data.get("certifications", []),
            "achievements": parsed_data.get("achievements", []),
            "languages": parsed_data.get("languages", []),
        }

    @staticmethod
    def _mark_failed(curation, error_code):
        try:
            curation.tailored_resume_error_code = error_code
            curation.save(update_fields=["tailored_resume_error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist job-tailored resume failure state.")

    @classmethod
    def build_system_baseline_resume(cls, candidate_source_data, curation, allowed_missing_skills=None):
        """
        Builds a 100% genuine, deterministic tailored resume directly from
        candidate_source_data with no AI call:
        1. Formats experience, projects, education, and certifications into standard ATS
           {source_index, source_text, tailored_text, header, bullets} entries.
        2. Prioritizes candidate skills that match the target job requirements at the top.
        3. Appends approved missing skills if enabled.
        4. Provides a clean summary referencing the candidate's core strengths for the role.
        """
        import re
        from resumes.resume_content import normalize_entry, entry_text
        from resumes.services.job_gap_analysis_service import TECH_ALIASES

        # 1. Skills prioritization
        candidate_skills = list(candidate_source_data.get("skills", []))
        analysis = curation.analysis_data or {}
        job_target_terms = set()
        for cat in (
            "required_skills", "preferred_skills", "technologies", "frameworks",
            "databases", "cloud_technologies", "tools", "ats_relevant_terms", "important_keywords"
        ):
            for item in analysis.get(cat, []):
                if isinstance(item, str) and item.strip():
                    job_target_terms.add(ResumeCurationAIService._normalise_text(item))

        matching_skills = []
        other_skills = []
        for skill in candidate_skills:
            norm = ResumeCurationAIService._normalise_text(skill)
            aliases = TECH_ALIASES.get(norm, set())
            is_match = (norm in job_target_terms) or any(
                ResumeCurationAIService._normalise_text(a) in job_target_terms for a in aliases
            )
            if is_match:
                if skill not in matching_skills:
                    matching_skills.append(skill)
            else:
                if skill not in other_skills:
                    other_skills.append(skill)

        ordered_skills = matching_skills + other_skills
        if allowed_missing_skills:
            for s in allowed_missing_skills:
                if s and s not in ordered_skills:
                    ordered_skills.append(s)

        # 2. Section formatting
        raw_sections = candidate_source_data.get("raw_sections") or {}
        sections = {}
        for field in ("experience", "projects", "education", "certifications"):
            items = raw_sections.get(field) or candidate_source_data.get(field, [])
            validated_entries = []
            for idx, raw_entry in enumerate(items):
                if isinstance(raw_entry, dict):
                    norm = normalize_entry(raw_entry)
                    hdr = norm["header"] if norm else ""
                    blts = norm["bullets"] if norm else []
                    full_txt = entry_text(norm) if norm else ""
                elif isinstance(raw_entry, str):
                    lines = [ln.strip() for ln in raw_entry.splitlines() if ln.strip()]
                    if len(lines) > 1 and any(lines[i].startswith(("-", "•", "*")) for i in range(1, len(lines))):
                        hdr = lines[0]
                        blts = [re.sub(r"^[-•*]\s*", "", ln) for ln in lines[1:] if ln.startswith(("-", "•", "*"))]
                        full_txt = raw_entry
                    else:
                        norm = normalize_entry(raw_entry)
                        hdr = norm["header"] if norm else str(raw_entry)
                        blts = norm["bullets"] if norm else []
                        full_txt = str(raw_entry)
                else:
                    hdr = str(raw_entry)
                    blts = []
                    full_txt = str(raw_entry)
                validated_entries.append({
                    "source_index": idx,
                    "source_text": full_txt,
                    "tailored_text": full_txt,
                    "header": hdr,
                    "bullets": blts,
                })
            sections[field] = validated_entries

        # 3. Summary
        existing_summary = candidate_source_data.get("summary")
        if existing_summary and str(existing_summary).strip():
            summary = str(existing_summary).strip()
        else:
            top_skills = ordered_skills[:4]
            skill_str = ", ".join(top_skills) if top_skills else "software engineering"
            summary = (
                f"Results-driven professional with proven expertise in {skill_str}. "
                f"Strong track record delivering high-quality solutions, contributing to "
                f"cross-functional teams, and achieving operational excellence in alignment with "
                f"{curation.job_title} requirements."
            )

        return {
            "summary": summary,
            "skills": ordered_skills,
            "achievements": list(candidate_source_data.get("achievements", [])),
            "languages": list(candidate_source_data.get("languages", [])),
            **sections,
        }
