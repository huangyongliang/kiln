import { createRouter, createWebHistory } from "vue-router";

import { allowRoute } from "./guard.js";
import AccessView from "../views/AccessView.vue";
import ArtifactDetailView from "../views/ArtifactDetailView.vue";
import ArtifactsView from "../views/ArtifactsView.vue";
import BuildDetailView from "../views/BuildDetailView.vue";
import BuildsView from "../views/BuildsView.vue";
import BuildTypesView from "../views/BuildTypesView.vue";
import ChatView from "../views/ChatView.vue";
import LoginView from "../views/LoginView.vue";
import ModelsView from "../views/ModelsView.vue";
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
    { path: "/models", name: "models", component: ModelsView },
    { path: "/build-types", name: "build-types", component: BuildTypesView },
    { path: "/chat", name: "chat", component: ChatView },
    { path: "/login", name: "login", component: LoginView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach(allowRoute);

export default router;
