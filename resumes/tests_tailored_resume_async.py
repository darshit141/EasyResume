from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from ai_jobs.models import AIJob
from resumes.models import JobResumeCuration, JobResumeCurationVersion, Resume
from resumes.serializers.job_curation_serializer import JobResumeCurationSerializer

User = get_user_model()


class TailoredResumeAsyncTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="async_user@example.com",
            password="StrongPassword123!",
        )
        self.client.force_authenticate(user=self.user)
        self.resume = Resume.objects.create(
            user=self.user,
            status="completed",
            parsed_data={"name": "Async Candidate", "skills": ["Python", "Django"]},
        )
        self.curation = JobResumeCuration.objects.create(
            user=self.user,
            resume=self.resume,
            job_title="Senior Python Backend Engineer",
            company_name="Tech Solutions",
            job_description="We need a strong Python and Django engineer with 5+ years of experience.",
            status="completed",
            gap_analysis_data={"overall_match_percentage": 78.5},
        )

    def test_curation_serializer_fields_without_versions_or_jobs(self):
        serializer = JobResumeCurationSerializer(self.curation)
        data = serializer.data
        self.assertEqual(data["job_title"], "Senior Python Backend Engineer")
        self.assertEqual(data["company_name"], "Tech Solutions")
        self.assertEqual(data["version_count"], 0)
        self.assertIsNone(data["latest_version"])
        self.assertEqual(data["match_percentage"], 78.5)
        self.assertIsNone(data["active_ai_job"])

    def test_curation_serializer_with_active_ai_job(self):
        job = AIJob.objects.create(
            user=self.user,
            job_type=AIJob.JOB_TYPE_TAILORED_RESUME_GENERATION,
            status=AIJob.STATUS_PROCESSING,
            curation=self.curation,
        )
        serializer = JobResumeCurationSerializer(self.curation)
        data = serializer.data
        self.assertIsNotNone(data["active_ai_job"])
        self.assertEqual(data["active_ai_job"]["id"], job.id)
        self.assertEqual(data["active_ai_job"]["status"], AIJob.STATUS_PROCESSING)
        self.assertEqual(data["active_ai_job"]["job_type"], AIJob.JOB_TYPE_TAILORED_RESUME_GENERATION)

    def test_curation_serializer_with_versions(self):
        JobResumeCurationVersion.objects.create(
            curation=self.curation,
            version=1,
            match_percentage=82.0,
            tailored_resume_data={"skills": ["Python"]},
        )
        JobResumeCurationVersion.objects.create(
            curation=self.curation,
            version=2,
            match_percentage=91.0,
            tailored_resume_data={"skills": ["Python", "Django"]},
        )
        serializer = JobResumeCurationSerializer(self.curation)
        data = serializer.data
        self.assertEqual(data["version_count"], 2)
        self.assertEqual(data["latest_version"], 2)
        self.assertEqual(data["match_percentage"], 91.0)

    def test_generate_endpoint_attaches_to_existing_in_flight_job(self):
        in_flight_job = AIJob.objects.create(
            user=self.user,
            job_type=AIJob.JOB_TYPE_TAILORED_RESUME_GENERATION,
            status=AIJob.STATUS_PROCESSING,
            curation=self.curation,
        )
        url = f"/api/resumes/job-curations/{self.curation.id}/tailored-resume/"
        response = self.client.post(url, {"regenerate": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["id"], in_flight_job.id)
        self.assertEqual(response.data["status"], AIJob.STATUS_PROCESSING)

    def test_ai_job_status_polling_serves_from_redis_cache(self):
        from django.core.cache import cache
        job = AIJob.objects.create(
            user=self.user,
            job_type=AIJob.JOB_TYPE_TAILORED_RESUME_GENERATION,
            status=AIJob.STATUS_PROCESSING,
            curation=self.curation,
        )
        # First poll queries DB and populates cache
        url = f"/api/ai-jobs/{job.id}/"
        res1 = self.client.get(url)
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data["status"], AIJob.STATUS_PROCESSING)

        # Mutate cached value in Redis to verify second poll is served from Redis
        cache_key = f"ai_job_poll:{job.id}"
        cached = cache.get(cache_key)
        self.assertIsNotNone(cached)
        cached["data"]["status"] = "cached_test_status"
        cache.set(cache_key, cached, timeout=60)

        res2 = self.client.get(url)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["status"], "cached_test_status")

    def test_api_key_pool_round_robin_rotation(self):
        from resumes.services.llm_service import APIKeyPool
        pool = APIKeyPool(["key_a", "key_b", "key_c"], provider_name="test_rr")
        # Should cycle through the keys in round-robin order
        first_cycle = [pool.active_keys()[0] for _ in range(3)]
        self.assertEqual(len(set(first_cycle)), 3)
        self.assertSetEqual(set(first_cycle), {"key_a", "key_b", "key_c"})
