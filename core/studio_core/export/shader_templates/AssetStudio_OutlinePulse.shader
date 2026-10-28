// AI Asset Studio — pulsing outline from the alpha edge (needs _Width px of empty border around each sprite).
Shader "AssetStudio/Sprite/OutlinePulse"
{
    Properties
    {
        [PerRendererData] _MainTex ("Sprite Texture", 2D) = "white" {}
        _OutlineColor ("Outline Color", Color) = (1, 0.85, 0.3, 1)
        _Width ("Width (px)", Range(0, 8)) = 1
        _Speed ("Pulse Speed (Hz)", Range(0, 20)) = 1.5
        _MinAlpha ("Pulse Min Alpha", Range(0, 1)) = 0.25
    }

    SubShader
    {
        Tags { "Queue" = "Transparent" "RenderType" = "Transparent" "RenderPipeline" = "UniversalPipeline" "IgnoreProjector" = "True" "PreviewType" = "Plane" "CanUseSpriteAtlas" = "True" }

        Blend SrcAlpha OneMinusSrcAlpha, One OneMinusSrcAlpha
        Cull Off
        ZWrite Off

        Pass
        {
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #pragma multi_compile _ SKINNED_SPRITE

            #include "Packages/com.unity.render-pipelines.universal/Shaders/2D/Include/Core2D.hlsl"

            struct Attributes
            {
                float3 positionOS : POSITION;
                float2 uv : TEXCOORD0;
                half4 color : COLOR;
                UNITY_VERTEX_INPUT_INSTANCE_ID
                UNITY_SKINNED_VERTEX_INPUTS
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                half4 color : COLOR;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            TEXTURE2D(_MainTex);
            SAMPLER(sampler_MainTex);

            CBUFFER_START(UnityPerMaterial)
                float4 _MainTex_TexelSize;
                half4 _OutlineColor;
                float _Width;
                half _Speed;
                half _MinAlpha;
            CBUFFER_END

            half Tap(float2 uv)
            {
                return SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, uv).a;
            }

            Varyings vert(Attributes v)
            {
                Varyings o = (Varyings)0;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                UNITY_SKINNED_VERTEX_COMPUTE(v);
                SetUpSpriteInstanceProperties();
                v.positionOS = UnityFlipSprite(v.positionOS, unity_SpriteProps.xy);
                o.positionCS = TransformObjectToHClip(v.positionOS);
                o.uv = v.uv;
                o.color = v.color * unity_SpriteColor;
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                half4 c = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv);
                float2 d = _MainTex_TexelSize.xy * _Width;
                half a = max(max(Tap(i.uv + float2(d.x, 0)), Tap(i.uv - float2(d.x, 0))),
                             max(Tap(i.uv + float2(0, d.y)), Tap(i.uv - float2(0, d.y))));
                a = max(a, max(max(Tap(i.uv + d * 0.7071), Tap(i.uv - d * 0.7071)),
                               max(Tap(i.uv + float2(d.x, -d.y) * 0.7071), Tap(i.uv + float2(-d.x, d.y) * 0.7071))));
                half pulse = lerp(_MinAlpha, 1.0, 0.5 + 0.5 * sin(_Time.y * _Speed * TWO_PI));
                half oa = saturate(a - c.a) * _OutlineColor.a * pulse;
                half outA = c.a + oa * (1.0 - c.a);
                half3 rgb = (c.rgb * c.a + _OutlineColor.rgb * oa * (1.0 - c.a)) / max(outA, 0.0001);
                return half4(rgb, outA) * i.color;
            }
            ENDHLSL
        }
    }
}
