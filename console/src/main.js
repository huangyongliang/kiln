import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./fonts.css";
import "./styles.css";

createApp(App).use(router).mount("#app");
