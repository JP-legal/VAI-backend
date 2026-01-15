from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

from vai.campaigns.webhooks import unified_elevenlabs_webhook
from voices.views import (
    # Profiles / Voices
    EnsureProfile, UploadVoiceSample, CloneVoice, DeleteVoice, ResetVoice, ListVoices, ListMyVoices,
    # Agents
    CreateAgent, CreateAgentWithVoice, ListMyAgents, GetConversationToken, GeneratePromptFromLinks,
    # Calls
    StartBackendCall, UploadCallSample, UploadCallChunk, GeneratePromptFromLinks, GetEmbedByOwner,
    # Embeds
    PublicEmbedToken, PublicEmbedToken,
    SupportLogDownload
)
from voices.views.agents import SaveFromStaged, SaveReclone, DiscardTempClone, BuildCloneFromTemp, DeleteVoiceAndAgent, \
    UploadTempCloneSample, DefaultCloneToken
from voices.views.embeds import GetMyEmbed

urlpatterns = [
    path("admin/", admin.site.urls),

    # --- Auth ---
    path("api/auth/", include("vai.accounts.urls", namespace="accounts")),
    path("auth/", include("vai.accounts.urls")),  # legacy alias
    path("api/", include(("vai.leads.urls", "leads"), namespace="leads")),
    path('api/', include('vai.lists.urls')),
    path("api/", include("vai.phone_numbers.urls")),
    path("api/", include("vai.campaigns.urls")),
    path("api/webhooks/elevenlabs/post-call/", unified_elevenlabs_webhook, name="elevenlabs-post-call"),
    path("api/billing/", include("vai.billing.urls")),

    # --- Profiles & Voices ---
    path("api/profiles/ensure/", EnsureProfile.as_view(), name="ensure-profile"),
    path("api/profiles/<int:profile_id>/samples/", UploadVoiceSample.as_view(), name="upload-voice-sample"),
    path("api/profiles/<int:profile_id>/clone/", CloneVoice.as_view(), name="clone-voice"),
    path("api/profiles/<int:profile_id>/voice/", DeleteVoice.as_view(), name="delete-voice"),
    path("api/profiles/<int:profile_id>/voices/", ListVoices.as_view(), name="list-profile-voices"),

    # User-wide voices
    path("api/my/voices/", ListMyVoices.as_view(), name="list-my-voices"),

    # Reset
    path("api/voices/<int:profile_id>/reset/", ResetVoice.as_view(), name="reset-voice"),

    # --- Agents ---
    path("api/profiles/<int:profile_id>/agents/", CreateAgent.as_view(), name="create-agent"),
    path("api/agents/", CreateAgentWithVoice.as_view(), name="create-agent-with-voice"),
    path("api/my/agents/", ListMyAgents.as_view(), name="list-my-agents"),
    path("api/agents/<str:agent_id>/conversation-token/", GetConversationToken.as_view(), name="conversation-token"),

    # --- Calls ---
    path("api/profiles/<int:profile_id>/start-backend-call/", StartBackendCall.as_view(), name="start-backend-call"),
    path("api/calls/<int:call_id>/sample/", UploadCallSample.as_view(), name="upload-call-sample"),
    path("api/calls/<int:call_id>/chunk/", UploadCallChunk.as_view(), name="upload-call-chunk"),
    path("api/embed/generate-prompt/", GeneratePromptFromLinks.as_view(), name="generate-prompt"),
    # --- Embeds ---
    path("api/embed/<str:public_id>/token/", PublicEmbedToken.as_view(), name="public-embed-token"),
    path('api/embeds/mine/', GetMyEmbed.as_view(), name='embeds-mine'),
    path("api/convai/default-token/", DefaultCloneToken.as_view()),
    path("api/clone/temp-sample/", UploadTempCloneSample.as_view()),
    path("api/clone/build", BuildCloneFromTemp.as_view()),
    path("api/clone/discard", DiscardTempClone.as_view()),
    path("api/voices/save-from-staged/", SaveFromStaged.as_view()),
    path("api/profiles/<int:profile_id>/voice+agent/", DeleteVoiceAndAgent.as_view()),
    path("api/profiles/<int:profile_id>/save-reclone/", SaveReclone.as_view()),

    # path("api/embed/generate-prompt/", generate_prompt_from_links),
    
]

# Static/media files in dev
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)



from voices.views import ListSupportLogs, SupportLogDetail
urlpatterns += [
    path("api/support/logs/", ListSupportLogs.as_view(), name="support-logs"),
    path("api/support/logs/<int:call_id>/", SupportLogDetail.as_view(), name="support-log-detail"),
    path("api/support/logs/<int:call_id>/download/", SupportLogDownload.as_view(), name="support-log-download"),
    # no trailing slash per your example

]

from voices.views import GetEmbedByProfile, SaveEmbed

# project urls.py (where you already included voices stuff)
from voices.views import (
    # elevenlabs_post_call_webhook_v3,
    ListSupportLogs,
    SupportLogDetail,
)

urlpatterns += [
    # path("api/webhooks/elevenlabs/_v3/post-call/", elevenlabs_post_call_webhook_v3, name="elevenlabs-post-call-v3"),
    path("api/support/logs/", ListSupportLogs.as_view(), name="support-logs"),
    path("api/support/logs/<int:call_id>/", SupportLogDetail.as_view(), name="support-log-detail"),
    path("api/embeds/by-profile/<int:profile_id>/", GetEmbedByProfile.as_view(), name="get_embed_by_profile"),
    path("api/embeds/save/", SaveEmbed.as_view(), name="save_embed"),
    path("api/embeds/by-owner/<int:owner_id>/", GetEmbedByOwner.as_view(), name="get_embed_by_owner"),
    
]
from django.conf import settings
from django.conf.urls.static import static
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# project urls.py
from voices.views import ListMyAgentRows

urlpatterns += [
    path("api/my/agent-rows/", ListMyAgentRows.as_view(), name="list-my-agent-rows"),
]
