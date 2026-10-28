// AI Asset Studio — noise dissolve. Tween _Progress 0 -> 1; _Speed > 0 loops it on its own.
Shader "AssetStudio/Sprite/Dissolve"
{
    Properties
    {
        [PerRendererData] _MainTex ("Sprite Texture", 2D) = "white" {}
        _Progress ("Progress", Range(0, 1)) = 0
        _EdgeColor ("Edge Color", Color) = (1, 0.55, 0.1, 1)
        _EdgeWidth ("Edge Width", Range(0, 0.5)) = 0.06
        _NoiseScale ("Noise Cell (px)", Range(1, 64)) = 6
        _Speed ("Loop Speed (Hz, 0 = off)", Range(0, 20)) = 0
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
                half _Progress;
                half4 _EdgeColor;
                half _EdgeWidth;
                float _NoiseScale;
                half _Speed;
            CBUFFER_END

            float Hash21(float2 p)
            {
                p = frac(p * float2(123.34, 456.21));
                p += dot(p, p + 45.32);
                return frac(p.x * p.y);
            }

            float ValueNoise(float2 p)
            {
                float2 i = floor(p);
                float2 f = frac(p);
                f = f * f * (3.0 - 2.0 * f);
                float a = Hash21(i);
                float b = Hash21(i + float2(1, 0));
                float c = Hash21(i + float2(0, 1));
                float d = Hash21(i + float2(1, 1));
                return lerp(lerp(a, b, f.x), lerp(c, d, f.x), f.y);
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
                half4 c = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, i.uv) * i.color;
                float2 px = i.uv * _MainTex_TexelSize.zw / _NoiseScale;
                float n = ValueNoise(px) * 0.65 + ValueNoise(px * 2.0 + 17.0) * 0.35;
                float p = _Speed > 0 ? abs(frac(_Time.y * _Speed * 0.5) * 2.0 - 1.0) : _Progress;
                float d = n - lerp(-_EdgeWidth, 1.0, p);
                half keep = step(0.0, d);
                half edge = keep * (1.0 - step(_EdgeWidth, d));
                return half4(lerp(c.rgb, _EdgeColor.rgb, edge * _EdgeColor.a), c.a * keep);
            }
            ENDHLSL
        }
    }
}
