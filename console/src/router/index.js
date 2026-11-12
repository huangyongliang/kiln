import { createRouter, createWebHistory } from "vue-router";

import AccessView from "../views/AccessView.vue";
import ArtifactsView from "../views/ArtifactsView.vue";
import BuildsView from "../views/BuildsView.vue";
import ChatView from "../views/ChatView.vue";
import OverviewView from "../views/OverviewView.vue";
import SkillsView from "../views/SkillsView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "overview", component: OverviewView },
    { path: "/builds", name: "builds", component: BuildsView },
    { path: "/artifacts", name: "artifacts", component: ArtifactsView },
    { path: "/skills", name: "skills", component: SkillsView },
    { path: "/access", name: "access", component: AccessView },
    { path: "/chat", name: "chat", component: ChatView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

export default router;
