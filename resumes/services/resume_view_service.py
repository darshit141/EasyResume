from django.http import Http404
from resumes.models import Resume

class ResumeViewService:
    """
    Service for retrieving the logged-in user's resume.
    """

    @staticmethod
    def get_resume(user):
        # Multiple records hone par crash hone ki jagah latest resume return karega
        resume = Resume.objects.filter(user=user).order_by("-id").first()
        
        if not resume:
            raise Http404("No resume found for this user.")
            
        return resume