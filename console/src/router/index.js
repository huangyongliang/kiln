import { createRouter, createWebHistory } from "vue-router";

import { ensureAccess } from "../access";
import AccessView from "../views/AccessView.vue";
import ArtifactDetailView from "../views/ArtifactDetailView.vue";
import ArtifactsView from "../views/ArtifactsView.vue";
import BuildDetailView from "../views/BuildDetailView.vue";
import BuildsView from "../views/BuildsView.vue";
import ChatView from "../views/ChatView.vue";
import LoginView from "../views/LoginView.vue";
import OverviewView from "../views/OverviewView.vue";
import SkillDetailView from "../views/SkillDetailView.vue";
import SkillNewView from "../views/SkillNewView.vue";
import SkillsView from "../views/SkillsView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "overview", component: OverviewView },
    { path: "/builds", name: "builds", component: BuildsView },
    { path: "/builds/:id", name: "build", component: BuildDetailView },
    { path: "/artifacts", name: "artifacts", component: ArtifactsView },
    { path: "/artifacts/:id", name: "artifact", component: ArtifactDetailView },
    { path: "/skills", name: "skills", component: SkillsView },
    { path: "/skills/new", name: "skill-new", component: SkillNewView },
    { path: "/skills/:id", name: "skill", component: SkillDetailView },
    { path: "/access", name: "access", component: AccessView },
    { path: "/chat", name: "chat", component: ChatView },
    { path: "/login", name: "login", component: LoginView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach(async (to) => {
  if (to.name === "login") {
    return true;
  }
  try {
    await ensureAccess();
  } catch (caught) {
    if (caught?.status === 401) {
      // The request helper already starts a full navigation to /login and
      // stores the expiry notice. Abort this client navigation so the notice
      // is not consumed by a page that is about to unload.
      return false;
    }
  }
  return true;
});

export default router;
